import hashlib
import hmac
import io
import json
import os
import re
import secrets as pysecrets
import time
import zipfile
from datetime import datetime

import pandas as pd
import requests
import streamlit as st

st.set_page_config(page_title="Suivi livraisons carrières", page_icon="🚚", layout="wide")
st.title("🚚 Suivi des livraisons – Matériaux de carrière")

TYPES = ["xlsx", "xlsm", "xls"]

MOTS_ENTETE = ["date", "client", "produit", "désignation", "designation", "quant", "qté", "qte",
               "prix", "p.u", "montant", "ht", "net", "tonnage", "matière", "matiere", "bl", "n°",
               "camion", "total", "tva", "ttc"]
# feuilles ignorées par défaut (elles recopient souvent les autres feuilles => totaux doublés)
MOTS_RECAP = ["recap", "récap", "total", "cumul", "synth", "bilan", "global"]

st.session_state.setdefault("cle", 0)

# =====================================================================
# BASE DE DONNÉES SUPABASE (stockage permanent)
# =====================================================================
TABLE = "livraisons_carrieres"
COLS_LIV = ("id,carriere,date_livraison,client,produit,qte_tonnes,qte_m3,montant_ht,chantier,"
            "fichier,feuille,created_at")
SANS_FICHIER = "(sans fichier)"


def _config():
    try:
        s = st.secrets["supabase"]
        return s["url"].rstrip("/") + "/rest/v1", s["key"]
    except Exception:
        st.error("Configuration Supabase manquante : ajoutez la section [supabase] "
                 "(url, key) dans les Secrets de l'application.")
        st.stop()


def _entetes(extra=None):
    _, cle_api = _config()
    h = {"apikey": cle_api}
    if cle_api.startswith("eyJ"):  # ancienne clé JWT (service_role) ; les nouvelles clés sb_secret_ n'en ont pas besoin
        h["Authorization"] = f"Bearer {cle_api}"
    return {**h, **(extra or {})}


def sb_lire(table, params=None):
    """Lit toute une table par pages de 1000 lignes."""
    base, _ = _config()
    lignes, debut, pas = [], 0, 1000
    while True:
        r = requests.get(f"{base}/{table}", params=params, timeout=60,
                         headers=_entetes({"Range-Unit": "items",
                                           "Range": f"{debut}-{debut + pas - 1}"}))
        if r.status_code == 416:
            return lignes
        r.raise_for_status()
        lot = r.json()
        lignes.extend(lot)
        if len(lot) < pas:
            return lignes
        debut += pas


def sb_ecrire(methode, table, params=None, json_=None, extra=None):
    base, _ = _config()
    r = requests.request(methode, f"{base}/{table}", params=params, json=json_, timeout=120,
                         headers=_entetes({"Content-Type": "application/json",
                                           "Prefer": "return=minimal", **(extra or {})}))
    if not r.ok:
        raise RuntimeError(f"Supabase {r.status_code} : {r.text[:300]}")


@st.cache_data(ttl=30, show_spinner=False)
def _kv_lire(cle):
    rows = sb_lire("kv", {"select": "valeur", "cle": f"eq.{cle}"})
    return rows[0]["valeur"] if rows else None


def kv_get(cle, defaut):
    v = _kv_lire(cle)
    return defaut if v is None else v


def kv_set(cle, valeur):
    sb_ecrire("POST", "kv", params={"on_conflict": "cle"}, json_={"cle": cle, "valeur": valeur},
              extra={"Prefer": "resolution=merge-duplicates,return=minimal"})
    _kv_lire.clear()


@st.cache_data(ttl=3600, show_spinner="Chargement des livraisons…")
def charge_livraisons():
    d = pd.DataFrame(sb_lire(TABLE, {"select": COLS_LIV, "order": "id"}),
                     columns=COLS_LIV.split(","))
    for c in ("qte_tonnes", "qte_m3", "montant_ht"):
        d[c] = pd.to_numeric(d[c], errors="coerce")
    d["created_at"] = pd.to_datetime(d["created_at"], errors="coerce", utc=True)
    d["fichier"] = d["fichier"].where(d["fichier"].notna() & (d["fichier"] != ""), SANS_FICHIER)
    d["carriere"] = d["carriere"].where(d["carriere"].notna() & (d["carriere"] != ""), "Non classée")
    return d


def filtre_fichier(nom):
    return {"fichier": "is.null"} if nom == SANS_FICHIER else {"fichier": f"eq.{nom}"}


def sb_supprime_ids(ids):
    for i in range(0, len(ids), 150):
        lot = ",".join(str(int(x)) for x in ids[i:i + 150])
        sb_ecrire("DELETE", TABLE, params={"id": f"in.({lot})"})


try:
    _kv_lire("comptes")
except Exception as e:
    st.error(f"Impossible de joindre la base Supabase : {e}")
    st.stop()


# =====================================================================
# AUTHENTIFICATION ET COMPTES UTILISATEURS
# =====================================================================
ITERATIONS = 200_000
MAX_TENTATIVES = 5
RE_LOGIN = re.compile(r"^[a-z0-9_.-]{3,30}$")
ROLES = {"user": "Utilisateur", "admin": "Administrateur"}
ALPHABET = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKMNPQRSTUVWXYZ23456789"

EXEMPLE_SECRETS = """[users.admin]
name = "Administrateur"
role = "admin"
password_hash = "COLLER_ICI_LE_HASH"
"""


def msg(typ, texte):
    st.session_state.setdefault("msgs", []).append((typ, texte))


def hash_mdp(mdp):
    sel = os.urandom(16)
    h = hashlib.pbkdf2_hmac("sha256", mdp.encode("utf-8"), sel, ITERATIONS)
    return f"pbkdf2_sha256${ITERATIONS}${sel.hex()}${h.hex()}"


def verifie_mdp(mdp, stocke):
    try:
        _, iters, sel, h = stocke.split("$")
        calc = hashlib.pbkdf2_hmac("sha256", mdp.encode("utf-8"), bytes.fromhex(sel), int(iters))
        return hmac.compare_digest(calc.hex(), h)
    except Exception:
        return False


def comptes_fichier():
    d = kv_get("comptes", {})
    return d if isinstance(d, dict) else {}


def sauve_comptes(d):
    kv_set("comptes", d)


def comptes_secrets():
    try:
        return {str(k).strip().lower(): dict(v) for k, v in st.secrets["users"].items()}
    except Exception:
        return {}


def tous_comptes():
    """Comptes de l'application + comptes des Secrets (ces derniers sont prioritaires)."""
    c = {k: {**v, "source": "fichier"} for k, v in comptes_fichier().items()}
    for k, v in comptes_secrets().items():
        c[k] = {**v, "source": "secrets"}
    return c


def moi():
    return (st.session_state.get("user") or {}).get("login")


def bloc_generateur():
    mdp = st.text_input("Mot de passe à transformer en hash", type="password", key="gen_mdp")
    if mdp:
        if len(mdp) < 8:
            st.warning("Utilisez au moins 8 caractères.")
        st.code(f'password_hash = "{hash_mdp(mdp)}"', language="toml")
        st.caption("Copiez cette ligne dans les Secrets, sous l'utilisateur concerné.")


def cb_genere():
    st.session_state["nu_mdp"] = "".join(pysecrets.choice(ALPHABET) for _ in range(10))


def cb_ajoute():
    ss = st.session_state
    login = ss.get("nu_login", "").strip().lower()
    nom = ss.get("nu_nom", "").strip() or login
    mdp = ss.get("nu_mdp", "")
    role = ss.get("nu_role", "user")
    if not RE_LOGIN.match(login):
        msg("error", "Identifiant invalide : 3 à 30 caractères (lettres minuscules, chiffres, . _ -).")
    elif login in tous_comptes():
        msg("error", f"L'identifiant « {login} » existe déjà.")
    elif len(mdp) < 8:
        msg("error", "Mot de passe trop court (8 caractères minimum).")
    else:
        d = comptes_fichier()
        d[login] = {"name": nom, "role": role, "password_hash": hash_mdp(mdp)}
        sauve_comptes(d)
        msg("success", f"Utilisateur créé. Communiquez-lui : identifiant « {login} » · "
                       f"mot de passe « {mdp} ».")
        for k in ("nu_login", "nu_nom", "nu_mdp"):
            ss[k] = ""


def cb_modifie(login):
    ss = st.session_state
    d = comptes_fichier()
    if login not in d:
        msg("error", "Compte introuvable.")
        return
    role = ss.get(f"ed_role_{login}", d[login].get("role", "user"))
    nouveau = ss.get(f"ed_mdp_{login}", "")
    if login == moi() and role != "admin":
        msg("error", "Vous ne pouvez pas retirer votre propre rôle d'administrateur.")
    elif nouveau and len(nouveau) < 8:
        msg("error", "Mot de passe trop court (8 caractères minimum).")
    else:
        d[login]["name"] = ss.get(f"ed_nom_{login}", "").strip() or login
        d[login]["role"] = role
        texte = f"Compte « {login} » mis à jour."
        if nouveau:
            d[login]["password_hash"] = hash_mdp(nouveau)
            texte += f" Nouveau mot de passe : « {nouveau} »."
        sauve_comptes(d)
        ss[f"ed_mdp_{login}"] = ""
        msg("success", texte)


def cb_supprime(login):
    d = comptes_fichier()
    if login == moi():
        msg("error", "Vous ne pouvez pas supprimer votre propre compte.")
    elif login in d:
        del d[login]
        sauve_comptes(d)
        msg("success", f"Compte « {login} » supprimé.")


def cb_mon_mdp():
    ss = st.session_state
    u = ss.get("user")
    if not u:
        return
    d = comptes_fichier()
    cur = d.get(u["login"])
    new, new2 = ss.get("mm_new", ""), ss.get("mm_new2", "")
    if u.get("source") == "secrets" or cur is None:
        msg("warning", "Ce compte est permanent (Secrets) : son mot de passe se change là-bas.")
    elif not verifie_mdp(ss.get("mm_old", ""), cur.get("password_hash", "")):
        msg("error", "Mot de passe actuel incorrect.")
    elif new != new2:
        msg("error", "Les deux nouveaux mots de passe sont différents.")
    elif len(new) < 8:
        msg("error", "Nouveau mot de passe trop court (8 caractères minimum).")
    else:
        cur["password_hash"] = hash_mdp(new)
        sauve_comptes(d)
        msg("success", "Votre mot de passe a été changé.")
        for k in ("mm_old", "mm_new", "mm_new2"):
            ss[k] = ""


def connexion():
    comptes = tous_comptes()

    if st.session_state.get("user"):
        actuel = comptes.get(st.session_state["user"]["login"])
        if actuel:
            return {**st.session_state["user"], "name": actuel.get("name", moi()),
                    "role": actuel.get("role", "user")}
        st.session_state.clear()
        st.rerun()

    if not comptes:
        st.warning("🔧 Configuration initiale : aucun utilisateur n'est défini.")
        st.markdown("1. Saisissez un mot de passe ci-dessous pour obtenir son **hash**.  \n"
                    "2. Dans Streamlit Cloud : **Manage app → Settings → Secrets**, collez le "
                    "modèle suivant en remplaçant le hash.  \n"
                    "3. Enregistrez, puis rechargez cette page.")
        bloc_generateur()
        st.code(EXEMPLE_SECRETS, language="toml")
        st.stop()

    st.subheader("🔐 Connexion")
    with st.form("login"):
        login = st.text_input("Identifiant")
        mdp = st.text_input("Mot de passe", type="password")
        ok = st.form_submit_button("Se connecter", type="primary")

    if ok:
        n = st.session_state.get("tentatives", 0)
        if n >= MAX_TENTATIVES:
            st.error("Trop de tentatives échouées. Rechargez la page et réessayez plus tard.")
            st.stop()
        cle_login = login.strip().lower()
        u = comptes.get(cle_login)
        stocke = u.get("password_hash", "") if u else "x$1$00$00"
        if verifie_mdp(mdp, stocke) and u is not None:
            st.session_state["user"] = {"login": cle_login, "name": u.get("name", cle_login),
                                        "role": u.get("role", "user"),
                                        "source": u.get("source", "fichier")}
            st.session_state["tentatives"] = 0
            st.rerun()
        st.session_state["tentatives"] = n + 1
        time.sleep(1)
        st.error("Identifiant ou mot de passe incorrect.")
    st.stop()


user = connexion()
est_admin = user["role"] == "admin"
st.sidebar.markdown(f"👤 **{user['name']}**  \n"
                    f"<small>{ROLES.get(user['role'], 'Utilisateur')}</small>",
                    unsafe_allow_html=True)
if st.sidebar.button("🚪 Se déconnecter"):
    st.session_state.clear()
    st.rerun()
with st.sidebar.expander("🔒 Changer mon mot de passe"):
    if user.get("source") == "secrets":
        st.caption("Compte permanent (Secrets) : le mot de passe se modifie dans les Secrets.")
    else:
        st.text_input("Mot de passe actuel", type="password", key="mm_old")
        st.text_input("Nouveau mot de passe", type="password", key="mm_new")
        st.text_input("Confirmer le nouveau", type="password", key="mm_new2")
        st.button("Changer mon mot de passe", on_click=cb_mon_mdp)
if est_admin:
    with st.sidebar.expander("🔑 Hash pour le compte de secours (Secrets)"):
        bloc_generateur()


# ---------- Fonctions utilitaires ----------
def nom_sur(nom):
    return re.sub(r"[^\w\-. ()]", "_", os.path.basename(nom)).strip()


def empreinte_df(d):
    return hashlib.md5(d.astype(str).to_csv(index=False).encode("utf-8")).hexdigest()


def to_num(s):
    if s.dtype == object:
        s = (s.astype(str).str.replace("\u00a0", "", regex=False).str.replace(" ", "", regex=False))
        s = s.where(~s.str.contains(",", regex=False), s.str.replace(".", "", regex=False))
        s = s.str.replace(",", ".", regex=False)
    return pd.to_numeric(s, errors="coerce")


def texte_propre(serie):
    return serie.where(serie.notna(), "").astype(str).str.strip()


def unique_cols(cols):
    vus, out = {}, []
    for c in cols:
        c = str(c).strip()
        if c in ("", "nan") or c.startswith("Unnamed"):
            c = "Colonne"
        vus[c] = vus.get(c, 0) + 1
        out.append(c if vus[c] == 1 else f"{c}_{vus[c]}")
    return out


def detecte_entete(raw):
    meilleur, ligne = 0, 0
    for i in range(min(len(raw), 40)):
        score = sum(any(m in str(v).lower() for m in MOTS_ENTETE)
                    for v in raw.iloc[i].tolist() if pd.notna(v) and isinstance(v, str))
        if score > meilleur:
            meilleur, ligne = score, i
    return ligne if meilleur >= 2 else 0


# ---------- Carrières (BS1, BS2, BS3, TG, KM, BA) ----------
CARRIERES = ["BS1", "BS2", "BS3", "TG", "KM", "BA"]
NON_CLASSEE = "Non classée"
MOIS_FR = ["JANVIER", "FÉVRIER", "MARS", "AVRIL", "MAI", "JUIN", "JUILLET", "AOÛT",
           "SEPTEMBRE", "OCTOBRE", "NOVEMBRE", "DÉCEMBRE"]


def detecte_carriere(nom):
    n = nom.upper()
    trouvees = {"BS" + m.group(1)
                for m in re.finditer(r"(?<![A-Z0-9])BS\s*[-_.]?\s*([123])(?![0-9])", n)}
    for code in ("TG", "KM", "BA"):
        if re.search(rf"(?<![A-Z0-9]){code}(?![A-Z0-9])", n):
            trouvees.add(code)
    return next(iter(trouvees)) if len(trouvees) == 1 else None


# ---------- Lecture d'un fichier Excel => lignes prêtes pour la base ----------
def devine_col(cols, mots):
    for m in mots:
        for c in cols:
            if m in c.lower():
                return c
    return None


def transforme_feuille(d, car, nom, feuille):
    """Retourne (lignes, motif_si_ignorée, nb_lignes_sans_date)."""
    cols = list(d.columns)
    c_client = devine_col(cols, ["client", "société", "societe", "raison", "destinataire",
                                 "tiers", "chantier"])
    c_produit = devine_col(cols, ["produit", "désignation", "designation", "article", "matière",
                                  "matiere", "nature"])
    c_date = devine_col(cols, ["date"])
    c_qte = devine_col(cols, ["qté en t", "qte en t", "tonnage", "quant", "qté", "qte", "poids"])
    c_pu = devine_col(cols, ["p.u ht", "p.u", "prix", "pu"])
    c_net = devine_col(cols, ["montant ht net", "ht net", "net ht", "montant ht", "total ht",
                              "montant"])
    c_chantier = devine_col(cols, ["chantier", "destination", "lieu"])
    manque = [n for n, c in (("Client", c_client), ("Produit", c_produit), ("Date", c_date))
              if c is None]
    if manque:
        return None, "colonne(s) non détectée(s) : " + ", ".join(manque), 0
    if c_net:
        montant = to_num(d[c_net])
    elif c_qte and c_pu:
        montant = to_num(d[c_qte]) * to_num(d[c_pu])
    else:
        return None, "colonne « Montant HT » introuvable", 0

    cl, pr = texte_propre(d[c_client]), texte_propre(d[c_produit])
    ok = (~cl.str.lower().isin(["", "nan", "none"])
          & ~cl.str.lower().str.contains("total")
          & ~pr.str.lower().str.contains("total"))
    dt = pd.to_datetime(d[c_date], errors="coerce", dayfirst=True)
    valide = dt.notna()
    nb_sans_date = int((ok & ~valide).sum())
    garde = ok & valide
    if not garde.any():
        return None, "aucune ligne exploitable", nb_sans_date

    dts = dt[garde]
    out = pd.DataFrame({
        "carriere": car,
        "mois_annee": [f"{MOIS_FR[x.month - 1]} -{x.year % 100:02d}" for x in dts],
        "date_livraison": dts.dt.strftime("%Y-%m-%d").to_numpy(),
        "client": cl[garde].to_numpy(),
        "produit": pr[garde].to_numpy(),
    })
    qte = to_num(d[c_qte])[garde].fillna(0).to_numpy() if c_qte else None
    en_m3 = bool(c_qte) and "m3" in c_qte.lower().replace("³", "3")
    out["qte_tonnes"] = qte if (qte is not None and not en_m3) else float("nan")
    out["qte_m3"] = qte if (qte is not None and en_m3) else float("nan")
    out["montant_ht"] = montant[garde].fillna(0).round(4).to_numpy()
    if c_chantier:
        ch = texte_propre(d[c_chantier])[garde]
        out["chantier"] = ch.where(~ch.str.lower().isin(["", "nan", "none", "nat"]), None).to_numpy()
    else:
        out["chantier"] = None
    out["fichier"] = nom
    out["feuille"] = feuille
    return out, "", nb_sans_date


def prepare_excel(contenu, nom, car, avec_recap=False):
    xl = pd.ExcelFile(io.BytesIO(contenu))
    morceaux, ignorees, vus, sans_date = [], [], set(), 0
    for feuille in xl.sheet_names:
        raw = xl.parse(feuille, header=None)
        d = xl.parse(feuille, header=detecte_entete(raw))
        d.columns = unique_cols(d.columns)
        d = d.dropna(how="all").dropna(axis=1, how="all")
        if d.empty:
            ignorees.append(f"{feuille} (vide)")
            continue
        if not avec_recap and any(m in feuille.lower() for m in MOTS_RECAP):
            ignorees.append(f"{feuille} (récapitulatif)")
            continue
        e = empreinte_df(d)
        if e in vus:
            ignorees.append(f"{feuille} (identique à une autre feuille)")
            continue
        vus.add(e)
        t, motif, nsd = transforme_feuille(d, car, nom, str(feuille))
        sans_date += nsd
        if t is None:
            ignorees.append(f"{feuille} ({motif})")
            continue
        morceaux.append(t)
    rows = pd.concat(morceaux, ignore_index=True) if morceaux else pd.DataFrame()
    return rows, ignorees, sans_date


def importer(contenu, nom, car, remplace_jours=True, ancien_nom=None, avec_recap=False):
    """Lit le fichier Excel et l'enregistre dans la base. Retourne (type_message, texte)."""
    try:
        rows, ignorees, sans_date = prepare_excel(contenu, nom, car, avec_recap)
    except Exception as e:
        return "error", f"« {nom} » : lecture impossible ({e})."
    if rows.empty:
        return "warning", (f"« {nom} » : aucune ligne exploitable, rien n'a été enregistré."
                           + (f" Feuilles ignorées : {', '.join(ignorees)}." if ignorees else ""))
    try:
        existant = charge_livraisons()
        a_suppr = set()
        if remplace_jours and not existant.empty:
            m = existant[(existant["carriere"] == car)
                         & existant["date_livraison"].isin(set(rows["date_livraison"]))]
            a_suppr.update(int(i) for i in m["id"])
        if ancien_nom:
            a_suppr.update(int(i) for i in existant.loc[existant["fichier"] == ancien_nom, "id"])
        enregistrements = json.loads(rows.to_json(orient="records", force_ascii=False))
        for i in range(0, len(enregistrements), 500):
            sb_ecrire("POST", TABLE, json_=enregistrements[i:i + 500])
        sb_supprime_ids(sorted(a_suppr))
    except Exception as e:
        return "error", f"« {nom} » : échec de l'enregistrement ({e})."
    finally:
        charge_livraisons.clear()
    texte = f"« {nom} » enregistré ({car}) : {len(rows)} ligne(s)"
    if a_suppr:
        texte += f", {len(a_suppr)} ancienne(s) ligne(s) remplacée(s)"
    texte += "."
    if sans_date:
        texte += f" {sans_date} ligne(s) sans date valide ignorée(s)."
    if ignorees:
        texte += f" Feuilles ignorées : {', '.join(ignorees)}."
    return "success", texte


def cb_carriere(nom, cle_widget):
    v = st.session_state.get(cle_widget)
    sb_ecrire("PATCH", TABLE, params=filtre_fichier(nom), json_={"carriere": v})
    charge_livraisons.clear()


def sauvegarde_zip():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("livraisons.csv", charge_livraisons().to_csv(index=False).encode("utf-8-sig"))
        z.writestr("bons_commande.json", json.dumps(charge_bc(), ensure_ascii=False, indent=2))
    return buf.getvalue()


# ---------- Bons de commande clients ----------
TOUS = "(tous les chantiers)"
STATUT_OK, STATUT_PROCHE = "🟢 OK", "🟡 Alerte (seuil atteint)"
STATUT_DEPASSE, STATUT_CLOS = "🔴 100 % atteint ou dépassé", "⚪ Clôturé"
COULEURS_STATUT = {STATUT_DEPASSE: "background-color: #f8d7da; color: #842029",
                   STATUT_PROCHE: "background-color: #fff3cd; color: #664d03"}


def charge_bc():
    d = kv_get("bons_commande", [])
    return d if isinstance(d, list) else []


def sauve_bc(liste):
    kv_set("bons_commande", liste)


def date_iso(v):
    return v.isoformat() if hasattr(v, "isoformat") else ""


def _bc_valide(ref, client, montant, periode, d1, d2, autres, ignorer_id=None):
    if not ref:
        return "Saisissez la référence du bon de commande."
    if not client:
        return "Choisissez le client."
    if montant <= 0:
        return "Le montant NET HT doit être supérieur à 0."
    if any(b["ref"].lower() == ref.lower() and b["id"] != ignorer_id for b in autres):
        return f"La référence « {ref} » existe déjà."
    if periode and d1 and d2 and d1 > d2:
        return "La date de début doit précéder la date de fin."
    return None


def cb_bc_ajoute():
    ss = st.session_state
    bcs = charge_bc()
    ref = (ss.get("bc_ref") or "").strip()
    client = ss.get("bc_client")
    chantier = ss.get(f"bc_chantier_{client}")
    montant = float(ss.get("bc_montant") or 0)
    periode = bool(ss.get("bc_periode"))
    d1, d2 = (ss.get("bc_d1"), ss.get("bc_d2")) if periode else (None, None)
    erreur = _bc_valide(ref, client, montant, periode, d1, d2, bcs)
    if erreur:
        msg("error", erreur)
        return
    bcs.append({"id": pysecrets.token_hex(4), "ref": ref, "client": client,
                "chantier": "" if chantier in (None, TOUS) else chantier,
                "montant": montant, "date_debut": date_iso(d1), "date_fin": date_iso(d2),
                "clos": False, "note": (ss.get("bc_note") or "").strip(),
                "cree_par": moi() or "", "cree_le": datetime.now().isoformat(timespec="seconds")})
    sauve_bc(bcs)
    msg("success", f"Bon de commande « {ref} » enregistré.")
    ss["bc_ref"], ss["bc_montant"], ss["bc_note"] = "", 0.0, ""


def cb_bc_modifie(bid):
    ss = st.session_state
    bcs = charge_bc()
    bc = next((b for b in bcs if b["id"] == bid), None)
    if bc is None:
        msg("error", "Bon de commande introuvable.")
        return
    ref = (ss.get(f"bce_ref_{bid}") or "").strip()
    montant = float(ss.get(f"bce_montant_{bid}") or 0)
    periode = bool(ss.get(f"bce_periode_{bid}"))
    d1, d2 = ((ss.get(f"bce_d1_{bid}"), ss.get(f"bce_d2_{bid}")) if periode else (None, None))
    erreur = _bc_valide(ref, bc["client"], montant, periode, d1, d2, bcs, ignorer_id=bid)
    if erreur:
        msg("error", erreur)
        return
    bc.update({"ref": ref, "montant": montant, "date_debut": date_iso(d1), "date_fin": date_iso(d2),
               "clos": bool(ss.get(f"bce_clos_{bid}")), "note": (ss.get(f"bce_note_{bid}") or "").strip()})
    sauve_bc(bcs)
    msg("success", f"Bon de commande « {ref} » mis à jour.")


def cb_bc_supprime(bid):
    bcs = charge_bc()
    reste = [b for b in bcs if b["id"] != bid]
    sauve_bc(reste)
    msg("success", "Bon de commande supprimé." if len(reste) < len(bcs) else "Bon introuvable.")


def colorie(d):
    d = d.drop(columns=["id"], errors="ignore")
    formats = {"Montant BC (HT net)": "{:,.2f}", "Livré (HT net)": "{:,.2f}", "Reste": "{:,.2f}",
               "Dépassement": "{:,.2f}", "% consommé": "{:.0f} %", "Qté livrée": "{:,.2f}"}
    try:
        return (d.style.apply(lambda r: [COULEURS_STATUT.get(r["Statut"], "")] * len(r), axis=1)
                .format({k: v for k, v in formats.items() if k in d.columns}, na_rep=""))
    except Exception:
        return d


def _fr(iso):
    return f"{iso[8:10]}/{iso[5:7]}/{iso[0:4]}"


def calcule_situation(base, bcs, seuil):
    colonnes = ["id", "Référence BC", "Client", "Chantier", "Période", "Montant BC (HT net)",
                "Livré (HT net)", "Reste", "Dépassement", "% consommé", "Statut", "Qté livrée"]
    qcol = next(iter(QTES), None)
    lignes = []
    for bc in bcs:
        sub = base[base["Client"] == bc["client"]]
        if bc.get("chantier") and "Chantier" in sub.columns:
            sub = sub[sub["Chantier"] == bc["chantier"]]
        if bc.get("date_debut"):
            sub = sub[sub["Jour"] >= bc["date_debut"]]
        if bc.get("date_fin"):
            sub = sub[sub["Jour"] <= bc["date_fin"]]
        livre = float(sub["Montant HT Net"].sum())
        montant = float(bc["montant"])
        pct = livre / montant * 100 if montant > 0 else 0.0
        if bc.get("clos"):
            statut = STATUT_CLOS
        elif round(livre, 2) >= round(montant, 2):
            statut = STATUT_DEPASSE
        elif pct >= seuil:
            statut = STATUT_PROCHE
        else:
            statut = STATUT_OK
        d1, d2 = bc.get("date_debut"), bc.get("date_fin")
        periode = ("toute la période" if not (d1 or d2) else
                   f"{_fr(d1) if d1 else '…'} → {_fr(d2) if d2 else '…'}")
        lignes.append({"id": bc["id"], "Référence BC": bc["ref"], "Client": bc["client"],
                       "Chantier": bc.get("chantier") or "(tous)", "Période": periode,
                       "Montant BC (HT net)": montant, "Livré (HT net)": livre,
                       "Reste": max(montant - livre, 0.0), "Dépassement": max(livre - montant, 0.0),
                       "% consommé": round(pct, 1), "Statut": statut,
                       "Qté livrée": float(sub[qcol].sum()) if qcol else None})
    return pd.DataFrame(lignes, columns=colonnes)


# =====================================================================
# 1) GESTION DES FICHIERS ENREGISTRÉS
# =====================================================================
for typ, texte in st.session_state.pop("msgs", []):
    getattr(st, typ)(texte)

cle = st.session_state["cle"]

if est_admin:
    with st.expander("👥 Gestion des utilisateurs"):
        comptes = tous_comptes()
        for login, u in sorted(comptes.items()):
            c1, c2, c3 = st.columns([5, 2, 2])
            role_txt = ROLES.get(u.get("role", "user"), "Utilisateur")
            badge = "  🔒 permanent (Secrets)" if u["source"] == "secrets" else ""
            c1.markdown(f"**{u.get('name', login)}** · `{login}` · {role_txt}{badge}")
            if u["source"] == "fichier":
                with c2.popover("✏️ Modifier"):
                    st.text_input("Nom affiché", value=u.get("name", login), key=f"ed_nom_{login}")
                    st.selectbox("Rôle", list(ROLES), format_func=ROLES.get,
                                 index=0 if u.get("role", "user") == "user" else 1,
                                 key=f"ed_role_{login}")
                    st.text_input("Nouveau mot de passe (vide = inchangé)", key=f"ed_mdp_{login}")
                    st.button("Enregistrer", key=f"ed_ok_{login}", on_click=cb_modifie,
                              args=(login,))
                with c3.popover("🗑️ Supprimer"):
                    st.write(f"Supprimer le compte **{login}** ?")
                    st.button("Oui, supprimer", key=f"sup_{login}", on_click=cb_supprime,
                              args=(login,))

        st.markdown("**➕ Nouvel utilisateur**")
        n1, n2 = st.columns(2)
        n1.text_input("Nom affiché", key="nu_nom", placeholder="ex. Ahmed Alaoui")
        n2.text_input("Identifiant (pour se connecter)", key="nu_login", placeholder="ex. ahmed")
        m1, m2 = st.columns([3, 1])
        m1.text_input("Mot de passe (8 caractères minimum)", key="nu_mdp")
        m2.button("🎲 Générer", on_click=cb_genere)
        st.selectbox("Rôle", list(ROLES), format_func=ROLES.get, key="nu_role")
        st.button("Créer l'utilisateur", type="primary", on_click=cb_ajoute)

        st.markdown("**💾 Sauvegarde des comptes**")
        st.caption("Les comptes créés ici sont stockés dans Supabase (permanent). Vous pouvez "
                   "tout de même télécharger une sauvegarde et la restaurer en un clic.")
        b1, b2 = st.columns(2)
        b1.download_button("⬇️ Télécharger la sauvegarde",
                           json.dumps(comptes_fichier(), ensure_ascii=False, indent=2),
                           file_name="comptes_utilisateurs.json", mime="application/json")
        sauv = b2.file_uploader("Restaurer une sauvegarde", type=["json"], key=f"restore_{cle}")
        if sauv is not None and b2.button("Restaurer"):
            try:
                data = json.loads(sauv.getvalue().decode("utf-8"))
                d = comptes_fichier()
                nb = 0
                for k, v in data.items():
                    k = str(k).strip().lower()
                    if (RE_LOGIN.match(k) and k not in comptes_secrets() and isinstance(v, dict)
                            and str(v.get("password_hash", "")).startswith("pbkdf2_sha256$")):
                        d[k] = {"name": v.get("name", k),
                                "role": "admin" if v.get("role") == "admin" else "user",
                                "password_hash": v["password_hash"]}
                        nb += 1
                sauve_comptes(d)
                msg("success", f"{nb} compte(s) restauré(s).")
            except Exception:
                msg("error", "Fichier de sauvegarde invalide.")
            st.session_state["cle"] += 1
            st.rerun()

data = charge_livraisons()
fichiers = sorted(data["fichier"].unique()) if not data.empty else []
st.sidebar.button("🔄 Actualiser les données", on_click=charge_livraisons.clear,
                  help="Recharge les données depuis Supabase (utile si vous les avez modifiées "
                       "directement dans Supabase).")

if est_admin:
    st.subheader("📁 Fichiers enregistrés dans la base")
    options_car = CARRIERES + [NON_CLASSEE]

    if fichiers:
        bilan = (data.groupby("fichier")
                 .agg(carriere=("carriere", "first"), lignes=("id", "size"),
                      montant=("montant_ht", "sum"), ajoute=("created_at", "min"))
                 .reset_index().sort_values(["carriere", "fichier"]).reset_index(drop=True))
        for i, r in bilan.iterrows():
            nom, actuelle = r["fichier"], r["carriere"]
            c1, c2, c3, c4 = st.columns([4.5, 1.6, 1.8, 1.8])
            ajoute = r["ajoute"].strftime("%d/%m/%Y %H:%M") if pd.notna(r["ajoute"]) else "—"
            nb = f"{int(r['lignes']):,}".replace(",", " ")
            mt = f"{r['montant']:,.0f}".replace(",", " ")
            c1.markdown(f"**📄 {nom}**  \n<small>{nb} lignes · {mt} Dh · ajouté le {ajoute}</small>",
                        unsafe_allow_html=True)
            opts = options_car if actuelle in options_car else options_car + [actuelle]
            c2.selectbox("Carrière", opts, index=opts.index(actuelle), key=f"car_{i}_{nom}",
                         label_visibility="collapsed", on_change=cb_carriere,
                         args=(nom, f"car_{i}_{nom}"))

            with c3.popover("✏️ Remplacer"):
                nouveau = st.file_uploader("Nouveau fichier Excel", type=TYPES, key=f"rep_{i}_{cle}")
                if nouveau is not None and st.button("Confirmer le remplacement", key=f"okrep_{i}"):
                    nouveau_nom = nom_sur(nouveau.name)
                    car = actuelle if actuelle in CARRIERES else detecte_carriere(nouveau_nom)
                    if nouveau_nom != nom and nouveau_nom in fichiers:
                        st.error("Un fichier portant ce nom existe déjà.")
                    elif car is None:
                        st.error("Choisissez d'abord la carrière de ce fichier dans la liste.")
                    else:
                        with st.spinner("Import en cours…"):
                            typ, texte = importer(nouveau.getvalue(), nouveau_nom, car,
                                                  remplace_jours=True,
                                                  ancien_nom=None if nom == SANS_FICHIER else nom)
                        msg(typ, texte)
                        st.session_state["cle"] += 1
                        st.rerun()

            with c4.popover("🗑️ Supprimer"):
                st.write(f"Supprimer **{nom}** et ses {nb} lignes ?")
                if st.button("Oui, supprimer", key=f"del_{i}"):
                    sb_ecrire("DELETE", TABLE, params=filtre_fichier(nom))
                    charge_livraisons.clear()
                    msg("success", f"« {nom} » a été supprimé.")
                    st.session_state["cle"] += 1
                    st.rerun()
    else:
        st.info("Aucun fichier enregistré. Ajoutez vos fichiers Excel ci-dessous.")

    with st.expander("➕ Ajouter des fichiers", expanded=not fichiers):
        ajouts = st.file_uploader("Fichier(s) Excel de suivi (une ou plusieurs carrières)",
                                  type=TYPES, accept_multiple_files=True, key=f"add_{cle}")
        choix_car = {}
        if ajouts:
            st.markdown("**Carrière de chaque fichier** (détectée d'après le nom, à corriger si besoin) :")
            opts = ["— choisir —"] + CARRIERES
            for j, up in enumerate(ajouts):
                det = detecte_carriere(up.name)
                choix_car[j] = st.selectbox(up.name, opts, index=opts.index(det) if det else 0,
                                            key=f"carAdd_{cle}_{j}")
        remplace_jours = st.checkbox(
            "Remplacer les jours déjà présents pour la même carrière (recommandé)", value=True,
            key=f"rj_{cle}",
            help="Évite les doublons quand un rapport cumulé est réimporté : les anciennes lignes "
                 "des mêmes jours (même carrière) sont remplacées par celles du nouveau fichier.")
        avec_recap = st.checkbox("Inclure aussi les feuilles « récapitulatif / total / cumul »",
                                 value=False, key=f"rc_{cle}")
        if ajouts and st.button("💾 Enregistrer dans la base", type="primary"):
            for j, up in enumerate(ajouts):
                car, nom_up = choix_car.get(j), nom_sur(up.name)
                if car not in CARRIERES:
                    msg("warning", f"« {up.name} » : choisissez sa carrière. Fichier non enregistré.")
                elif nom_up in fichiers:
                    msg("warning", f"Un fichier nommé « {nom_up} » existe déjà : utilisez "
                                   f"« Remplacer » pour le mettre à jour.")
                else:
                    with st.spinner(f"Import de {nom_up}…"):
                        typ, texte = importer(up.getvalue(), nom_up, car, remplace_jours,
                                              None, avec_recap)
                    msg(typ, texte)
            st.session_state["cle"] += 1
            st.rerun()

    with st.expander("💾 Sauvegarde (livraisons + bons de commande)"):
        st.caption("Les données sont stockées dans Supabase : elles ne disparaissent plus au "
                   "redémarrage. Téléchargez tout de même une copie de temps en temps.")
        if fichiers and st.button("📦 Préparer la sauvegarde (ZIP)"):
            st.session_state["zip_pret"] = sauvegarde_zip()
        if st.session_state.get("zip_pret"):
            st.download_button("⬇ Télécharger la sauvegarde", st.session_state["zip_pret"],
                               file_name=f"sauvegarde_livraisons_{datetime.now():%Y-%m-%d}.zip",
                               mime="application/zip")

if not fichiers:
    if not est_admin:
        st.info("Aucun fichier disponible pour le moment. Contactez l'administrateur.")
    st.stop()

st.divider()

# =====================================================================
# 2) CHOIX DES FICHIERS À ANALYSER ET PRÉPARATION DES DONNÉES
# =====================================================================
avance = st.sidebar.expander("⚙️ Fichiers utilisés (avancé)")
sel_fichiers = avance.multiselect("Fichiers à analyser", fichiers, default=fichiers)
if not sel_fichiers:
    st.warning("Sélectionnez au moins un fichier dans la barre latérale.")
    st.stop()

brut = data[data["fichier"].isin(sel_fichiers)]
dj = pd.to_datetime(brut["date_livraison"], errors="coerce")
df = pd.DataFrame({
    "Carrière": brut["carriere"],
    "Fichier": brut["fichier"],
    "Feuille": brut["feuille"].fillna(""),
    "Jour": dj.dt.strftime("%Y-%m-%d"),
    "Mois": dj.dt.strftime("%Y-%m"),
    "Client": texte_propre(brut["client"]),
    "Produit": texte_propre(brut["produit"]),
    "Chantier": texte_propre(brut["chantier"]),
    "Quantité (T)": brut["qte_tonnes"].fillna(0),
    "Quantité (m³)": brut["qte_m3"].fillna(0),
    "Montant HT Net": brut["montant_ht"].fillna(0),
})
sans_date = df[df["Jour"].isna()].copy()
df = df[df["Jour"].notna()].copy()

c_client, c_produit = "Client", "Produit"
df = df[~df[c_client].str.lower().isin(["", "nan", "none"])]
masque_total = (df[c_client].str.lower().str.contains("total")
                | df[c_produit].str.lower().str.contains("total"))
df = df[~masque_total].copy()
df["Chantier"] = df["Chantier"].where(~df["Chantier"].str.lower().isin(["", "nan", "none", "nat"]),
                                      "(non renseigné)")
c_chantier = "Chantier" if (df["Chantier"] != "(non renseigné)").any() else None
QTES = [q for q in ("Quantité (T)", "Quantité (m³)") if df[q].ne(0).any()]
UNITES = {"Quantité (T)": "T", "Quantité (m³)": "m³"}
c_qte = bool(QTES)
n_remplacees = 0

if df.empty:
    st.warning("Aucune ligne exploitable. Vérifiez les fichiers importés.")
    st.stop()

# ---------- Lignes en double ----------
colonnes_source = [c for c in df.columns if c not in ("Fichier", "Feuille")]
n_dup = int(df.duplicated(subset=colonnes_source).sum())
retirer = False
if n_dup:
    st.sidebar.warning(f"{n_dup} ligne(s) strictement identique(s) détectée(s).")
    retirer = st.sidebar.checkbox("Ignorer ces lignes en double", value=False,
                                  help="Attention : deux livraisons réelles parfaitement "
                                       "identiques seraient aussi retirées.")
if retirer:
    df = df.drop_duplicates(subset=colonnes_source)

# ---------- Vérification des totaux ----------
with st.expander("🔎 Vérification des totaux par carrière, fichier, feuille et mois"):
    agg = {"Lignes": ("Montant HT Net", "size"), "Montant_HT_Net": ("Montant HT Net", "sum")}
    for q in QTES:
        agg[q] = (q, "sum")
    verif = df.groupby(["Carrière", "Fichier", "Feuille", "Mois"]).agg(**agg).reset_index()
    st.dataframe(verif, use_container_width=True)
    if n_remplacees:
        st.info(f"{n_remplacees} ligne(s) ignorée(s) car un fichier plus récent de la même "
                f"carrière couvre les mêmes jours.")
    if len(sans_date):
        st.warning(f"{len(sans_date)} ligne(s) sans date valide ont été exclues du calcul "
                   f"(souvent la ligne de total du bas de la feuille) :")
        st.dataframe(sans_date, use_container_width=True)

# ---------- Filtres (liés entre eux) ----------
FILTRES = {"f_carriere": "Carrière", "f_mois": "Mois", "f_client": c_client,
           "f_produit": c_produit}
if c_chantier:
    FILTRES["f_chantier"] = "Chantier"


def options_possibles(cle):
    sub = df
    for k, col in FILTRES.items():
        choisi = st.session_state.get(k) or []
        if k != cle and choisi:
            sub = sub[sub[col].isin(choisi)]
    return sorted(sub[FILTRES[cle]].astype(str).unique(), key=str)


for _ in range(3):
    for k in FILTRES:
        valides = set(options_possibles(k))
        st.session_state[k] = [v for v in (st.session_state.get(k) or []) if v in valides]


def cb_reset_filtres():
    for k in FILTRES:
        st.session_state[k] = []


st.sidebar.subheader("Filtres")
st.sidebar.button("↺ Réinitialiser les filtres", on_click=cb_reset_filtres)
carrieres = st.sidebar.multiselect("Carrière", options_possibles("f_carriere"), key="f_carriere")
mois = st.sidebar.multiselect("Mois", options_possibles("f_mois"), key="f_mois")
clients = st.sidebar.multiselect("Client", options_possibles("f_client"), key="f_client")
produits = st.sidebar.multiselect("Produit", options_possibles("f_produit"), key="f_produit")
chantiers = (st.sidebar.multiselect("Chantier", options_possibles("f_chantier"), key="f_chantier")
             if c_chantier else [])

mn, mx = float(df["Montant HT Net"].min()), float(df["Montant HT Net"].max())
plage = st.sidebar.slider("Montant HT Net (par ligne)", mn, mx, (mn, mx)) if mn < mx else (mn, mx)

f = df.copy()
if carrieres:
    f = f[f["Carrière"].isin(carrieres)]
if mois:
    f = f[f["Mois"].isin(mois)]
if clients:
    f = f[f[c_client].isin(clients)]
if produits:
    f = f[f[c_produit].isin(produits)]
if chantiers:
    f = f[f["Chantier"].isin(chantiers)]
f = f[f["Montant HT Net"].between(plage[0], plage[1])]


def fmt(x):
    return f"{x:,.2f}".replace(",", " ")


# ---------- Bons de commande : situation et alertes ----------
bcs = charge_bc()
seuil_alerte = int(st.session_state.get("bc_seuil", 80))

# Calcul sur les données filtrées f
sit = calcule_situation(f, bcs, seuil_alerte)
n_rouge = n_jaune = 0
if not sit.empty:
    depasses = sit[sit["Statut"] == STATUT_DEPASSE]
    proches = sit[sit["Statut"] == STATUT_PROCHE]
    n_rouge, n_jaune = len(depasses), len(proches)
    if n_rouge:
        st.error("🔴 **Bon(s) de commande à 100 % ou plus** : " + " · ".join(
            f"{r['Référence BC']} ({r['Client']}) {r['% consommé']:.0f} %"
            for _, r in depasses.iterrows()))
    if n_jaune:
        st.warning(f"🟡 **Bon(s) de commande à {seuil_alerte} % ou plus** : " + " · ".join(
            f"{r['Référence BC']} ({r['Client']}) {r['% consommé']:.0f} %"
            for _, r in proches.iterrows()))

# ---------- Indicateurs ----------
k1, k2, k3, k4 = st.columns(4)
k1.metric("Montant HT Net total", fmt(f["Montant HT Net"].sum()))
k2.metric("Nb de lignes", f"{len(f):,}".replace(",", " "))
k3.metric("Nb de clients", f[c_client].nunique())
if QTES:
    k4.metric("Quantité totale", " · ".join(f"{fmt(f[q].sum())} {UNITES[q]}" for q in QTES))

# ---------- Onglets & Génération des tableaux ----------
MONTANT = "Montant HT Net"
indicateurs = [MONTANT] + QTES
ind = (st.radio("Indicateur affiché dans les graphiques et tableaux croisés", indicateurs,
                horizontal=True) if len(indicateurs) > 1 else MONTANT)
col_ind = ind if ind in QTES else "Montant HT Net"


def resume(cle):
    agg = {MONTANT: ("Montant HT Net", "sum")}
    for q in QTES:
        agg[q] = (q, "sum")
    agg["Livraisons"] = ("Montant HT Net", "size")
    return f.groupby(cle, as_index=False).agg(**agg)


def get_tableau_complet(cle, croise=None):
    r = resume(cle)
    r = r.sort_values(cle) if cle in ("Mois", "Jour") else r.sort_values(ind, ascending=False)
    total = {cle: "TOTAL", **{c: r[c].sum() for c in r.columns if c != cle}}
    df_table = pd.concat([r, pd.DataFrame([total])], ignore_index=True)
    
    df_croise = None
    if croise:
        df_croise = f.pivot_table(index=cle, columns=croise, values=col_ind, aggfunc="sum",
                                  fill_value=0, margins=True, margins_name="Total").reset_index()
    return r, df_table, df_croise


def afficher(cle, croise=None):
    r, df_table, df_croise = get_tableau_complet(cle, croise)
    st.bar_chart(r, x=cle, y=ind)
    st.dataframe(df_table, use_container_width=True, hide_index=True)
    if croise and df_croise is not None:
        st.markdown(f"**{cle} × {croise} — {ind}**")
        st.dataframe(df_croise, use_container_width=True)


TAB_ALERTES = f"🚨 Alertes ({n_rouge + n_jaune})" if (n_rouge + n_jaune) else "🚨 Alertes"
noms_onglets = [TAB_ALERTES, "📅 Par mois", "📆 Par jour", "🏭 Par carrière", "👥 Par client",
                "🪨 Par produit"]
if c_chantier:
    noms_onglets.append("🏗️ Par chantier")
noms_onglets.append("📑 Bons de commande")
noms_onglets.append("📋 Détail")
onglets = dict(zip(noms_onglets, st.tabs(noms_onglets)))

with onglets[TAB_ALERTES]:
    st.caption(f"🟡 **Jaune** : bon consommé à {seuil_alerte} % ou plus · 🔴 **Rouge** : 100 % atteint "
               f"ou dépassé. Le seuil jaune se règle dans l'onglet « Bons de commande ».")
    if sit.empty:
        st.info("Aucun bon de commande enregistré ou correspondant à la sélection.")
    else:
        al1, al2, al3 = st.columns(3)
        al1.metric("🔴 À 100 % ou plus", n_rouge)
        al2.metric("🟡 Au seuil d'alerte", n_jaune)
        al3.metric("Dépassement total (Dh)", fmt(sit.loc[sit["Statut"] != STATUT_CLOS, "Dépassement"].sum()))
        alertes = (sit[sit["Statut"].isin([STATUT_DEPASSE, STATUT_PROCHE])]
                   .sort_values("% consommé", ascending=False))
        if alertes.empty:
            st.success(f"✅ Aucun bon de commande n'a atteint {seuil_alerte} %.")
        else:
            if not c_qte:
                alertes = alertes.drop(columns=["Qté livrée"])
            st.dataframe(colorie(alertes), use_container_width=True, hide_index=True)

with onglets["📅 Par mois"]:
    afficher("Mois")
with onglets["📆 Par jour"]:
    afficher("Jour")
with onglets["🏭 Par carrière"]:
    afficher("Carrière", "Mois")
    st.markdown("**Dernier jour reçu par carrière**")
    dern = (f.groupby("Carrière").agg(Dernier_jour=("Jour", "max"), Jours_renseignés=("Jour", "nunique"))
            .reset_index())
    st.dataframe(dern, use_container_width=True, hide_index=True)
with onglets["👥 Par client"]:
    afficher(c_client, "Mois")
with onglets["🪨 Par produit"]:
    afficher(c_produit, "Client")
if c_chantier:
    with onglets["🏗️ Par chantier"]:
        afficher("Chantier", "Mois")
with onglets["📑 Bons de commande"]:
    st.caption("Pour chaque bon de commande, le montant livré (Montant HT Net des livraisons du "
               "client) est comparé au montant NET HT du bon selon les filtres sélectionnés.")
    st.number_input("Seuil d'alerte (% du bon déjà consommé)", min_value=10, max_value=100,
                    value=80, step=5, key="bc_seuil")
    if sit.empty:
        st.info("Aucun bon de commande enregistré." + (" Ajoutez-en un ci-dessous." if est_admin else ""))
    else:
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Bons actifs", int((sit["Statut"] != STATUT_CLOS).sum()))
        m2.metric("🔴 À 100 % ou plus", n_rouge)
        m3.metric("🟡 Au seuil d'alerte", n_jaune)
        m4.metric("Dépassement total (Dh)", fmt(sit.loc[sit["Statut"] != STATUT_CLOS, "Dépassement"].sum()))
        aff = sit.drop(columns=["id"]).sort_values("% consommé", ascending=False)
        if not c_qte:
            aff = aff.drop(columns=["Qté livrée"])
        st.dataframe(aff, use_container_width=True, hide_index=True, column_config={
            "Montant BC (HT net)": st.column_config.NumberColumn(format="%.2f"),
            "Livré (HT net)": st.column_config.NumberColumn(format="%.2f"),
            "Reste": st.column_config.NumberColumn(format="%.2f"),
            "Dépassement": st.column_config.NumberColumn(format="%.2f"),
            "% consommé": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.0f%%"),
        })

    if est_admin:
        with st.expander("➕ Ajouter un bon de commande", expanded=sit.empty):
            clients_liste = sorted(df["Client"].unique(), key=str)
            a1, a2 = st.columns(2)
            a1.text_input("Référence du bon de commande client", key="bc_ref",
                          placeholder="ex. BC-2026-0145")
            client_bc = a2.selectbox("Client", clients_liste, key="bc_client")
            a3, a4 = st.columns(2)
            a3.number_input("Montant NET HT du bon de commande (Dh)", min_value=0.0, step=1000.0,
                            format="%.2f", key="bc_montant")
            if c_chantier:
                chs = sorted(df.loc[df["Client"] == client_bc, "Chantier"].unique(), key=str)
                a4.selectbox("Chantier (optionnel)", [TOUS] + chs, key=f"bc_chantier_{client_bc}")
            st.checkbox("Limiter à une période (optionnel)", key="bc_periode")
            if st.session_state.get("bc_periode"):
                p1, p2 = st.columns(2)
                p1.date_input("Du", key="bc_d1")
                p2.date_input("Au", key="bc_d2")
            st.text_input("Note (optionnel)", key="bc_note")
            st.button("Enregistrer le bon de commande", type="primary", on_click=cb_bc_ajoute)

        if bcs:
            with st.expander("✏️ Modifier ou supprimer un bon de commande"):
                etiquettes = {b["id"]: f"{b['ref']} · {b['client']}" for b in bcs}
                bid = st.selectbox("Bon de commande", list(etiquettes), format_func=etiquettes.get,
                                   key="bc_sel")
                bc = next(b for b in bcs if b["id"] == bid)
                st.text_input("Référence", value=bc["ref"], key=f"bce_ref_{bid}")
                st.number_input("Montant NET HT (Dh)", min_value=0.0, step=1000.0, format="%.2f",
                                value=float(bc["montant"]), key=f"bce_montant_{bid}")
                st.checkbox("Clôturé (plus d'alerte pour ce bon)", value=bool(bc.get("clos")),
                            key=f"bce_clos_{bid}")
                a_periode = st.checkbox("Limiter à une période",
                                        value=bool(bc.get("date_debut") or bc.get("date_fin")),
                                        key=f"bce_periode_{bid}")
                if a_periode:
                    aujourdhui = datetime.today().date()
                    q1, q2 = st.columns(2)
                    q1.date_input("Du", key=f"bce_d1_{bid}", value=(
                        datetime.strptime(bc["date_debut"], "%Y-%m-%d").date()
                        if bc.get("date_debut") else aujourdhui))
                    q2.date_input("Au", key=f"bce_d2_{bid}", value=(
                        datetime.strptime(bc["date_fin"], "%Y-%m-%d").date()
                        if bc.get("date_fin") else aujourdhui))
                st.text_input("Note", value=bc.get("note", ""), key=f"bce_note_{bid}")
                s1, s2 = st.columns(2)
                s1.button("Enregistrer les modifications", type="primary",
                          on_click=cb_bc_modifie, args=(bid,))
                with s2.popover("🗑️ Supprimer ce bon"):
                    st.write(f"Supprimer **{bc['ref']}** ?")
                    st.button("Oui, supprimer", key=f"bc_del_{bid}", on_click=cb_bc_supprime,
                              args=(bid,))

with onglets["📋 Détail"]:
    st.dataframe(f, use_container_width=True)

# =====================================================================
# 3) EXPORT EXCEL COMPLET (CONFORME AUX TABLEAUX ET IMPRIMABLE EN A4 PAYSAGE)
# =====================================================================
buf = io.BytesIO()
with pd.ExcelWriter(buf, engine="openpyxl") as w:
    # 1. Détail filtré
    f.to_excel(w, sheet_name="Détail", index=False)
    
    # 2. Synthèses simples & croisées
    _, t_mois, _ = get_tableau_complet("Mois")
    t_mois.to_excel(w, sheet_name="Par mois", index=False)
    
    _, t_jour, _ = get_tableau_complet("Jour")
    t_jour.to_excel(w, sheet_name="Par jour", index=False)
    
    _, t_car, c_car = get_tableau_complet("Carrière", "Mois")
    t_car.to_excel(w, sheet_name="Par carrière", index=False)
    if c_car is not None:
        c_car.to_excel(w, sheet_name="Carrière x Mois", index=False)
        
    _, t_cli, c_cli = get_tableau_complet(c_client, "Mois")
    t_cli.to_excel(w, sheet_name="Par client", index=False)
    if c_cli is not None:
        c_cli.to_excel(w, sheet_name="Client x Mois", index=False)
        
    _, t_prod, c_prod = get_tableau_complet(c_produit, "Client")
    t_prod.to_excel(w, sheet_name="Par produit", index=False)
    if c_prod is not None:
        c_prod.to_excel(w, sheet_name="Produit x Client", index=False)
        
    if c_chantier:
        _, t_cha, c_cha = get_tableau_complet("Chantier", "Mois")
        t_cha.to_excel(w, sheet_name="Par chantier", index=False)
        if c_cha is not None:
            c_cha.to_excel(w, sheet_name="Chantier x Mois", index=False)
            
    # 3. Situation des Bons de Commande et Alertes
    if not sit.empty:
        sit.drop(columns=["id"], errors="ignore").to_excel(w, sheet_name="Bons de commande", index=False)
        alertes_exp = sit[sit["Statut"].isin([STATUT_DEPASSE, STATUT_PROCHE])].drop(columns=["id"], errors="ignore")
        if not alertes_exp.empty:
            alertes_exp.to_excel(w, sheet_name="Alertes", index=False)

    # 4. Application de la mise en page (A4, Paysage, Ajusté à la largeur) sur chaque feuille
    for ws in w.sheets.values():
        ws.page_setup.orientation = ws.ORIENTATION_LANDSCAPE
        ws.page_setup.paperSize = ws.PAPERSIZE_A4
        ws.page_setup.fitToPage = True
        ws.page_setup.fitToWidth = 1
        ws.page_setup.fitToHeight = 0
        ws.sheet_properties.pageSetUpPr.fitToPage = True
        # Répétition de la première ligne (en-têtes) sur toutes les pages imprimées
        ws.print_title_rows = '1:1'

st.download_button("⬇️ Exporter la sélection (Excel)", buf.getvalue(),
                   file_name=f"livraisons_filtrees_{datetime.now():%Y%m%d_%H%M}.xlsx",
                   mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
