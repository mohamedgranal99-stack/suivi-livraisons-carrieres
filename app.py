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
import streamlit as st

st.set_page_config(page_title="Suivi livraisons carrières", page_icon="🚚", layout="wide")
st.title("🚚 Suivi des livraisons – Matériaux de carrière")

DOSSIER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "donnees_enregistrees")
os.makedirs(DOSSIER, exist_ok=True)
TYPES = ["xlsx", "xlsm", "xls"]
EXT = tuple("." + t for t in TYPES)

MOTS_ENTETE = ["date", "client", "produit", "désignation", "designation", "quant", "qté", "qte",
               "prix", "p.u", "montant", "ht", "net", "tonnage", "matière", "matiere", "bl", "n°",
               "camion", "total", "tva", "ttc"]
# feuilles ignorées par défaut (elles recopient souvent les autres feuilles => totaux doublés)
MOTS_RECAP = ["recap", "récap", "total", "cumul", "synth", "bilan", "global"]

st.session_state.setdefault("cle", 0)

# =====================================================================
# AUTHENTIFICATION ET COMPTES UTILISATEURS
#   - compte « de secours » permanent : défini dans les Secrets de Streamlit
#   - autres comptes : créés / modifiés / supprimés par l'administrateur dans l'application
# =====================================================================
ITERATIONS = 200_000
MAX_TENTATIVES = 5
DOSSIER_COMPTES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "comptes_utilisateurs")
os.makedirs(DOSSIER_COMPTES, exist_ok=True)
FICHIER_COMPTES = os.path.join(DOSSIER_COMPTES, "utilisateurs.json")
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
    try:
        with open(FICHIER_COMPTES, encoding="utf-8") as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def sauve_comptes(d):
    tmp = FICHIER_COMPTES + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(d, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, FICHIER_COMPTES)


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


# ---------- Actions (appelées par les boutons) ----------
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
        if actuel:   # le rôle / nom sont relus à chaque fois (compte modifié par l'admin)
            return {**st.session_state["user"], "name": actuel.get("name", moi()),
                    "role": actuel.get("role", "user")}
        st.session_state.clear()   # compte supprimé : déconnexion
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
        stocke = u.get("password_hash", "") if u else "x$1$00$00"   # même durée si inconnu
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


def liste_fichiers():
    return sorted(f for f in os.listdir(DOSSIER) if f.lower().endswith(EXT))


def empreinte_octets(contenu):
    return hashlib.md5(contenu).hexdigest()


def empreinte_fichier(chemin):
    with open(chemin, "rb") as fh:
        return empreinte_octets(fh.read())


def empreinte_df(d):
    return hashlib.md5(d.astype(str).to_csv(index=False).encode("utf-8")).hexdigest()


def info_fichier(chemin):
    st_ = os.stat(chemin)
    ko = st_.st_size / 1024
    taille = f"{ko:,.0f} Ko".replace(",", " ")
    date = datetime.fromtimestamp(st_.st_mtime).strftime("%d/%m/%Y %H:%M")
    return f"{taille} · enregistré le {date}"


def to_num(s):
    if s.dtype == object:
        s = (s.astype(str).str.replace("\u00a0", "", regex=False).str.replace(" ", "", regex=False))
        # "2.719,36" -> 2719.36 ; "2719,36" -> 2719.36
        s = s.where(~s.str.contains(",", regex=False), s.str.replace(".", "", regex=False))
        s = s.str.replace(",", ".", regex=False)
    return pd.to_numeric(s, errors="coerce")


def texte_propre(serie):
    """Texte sans espaces ; cellule vide -> chaîne vide."""
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


@st.cache_data(show_spinner="Lecture du fichier…")
def lire_classeur(chemin, mtime):
    """Lit toutes les feuilles d'un classeur (en-tête détecté automatiquement)."""
    xl = pd.ExcelFile(chemin)
    res = {}
    for nom in xl.sheet_names:
        raw = xl.parse(nom, header=None)
        h = detecte_entete(raw)
        d = xl.parse(nom, header=h)
        d.columns = unique_cols(d.columns)
        d = d.dropna(how="all").dropna(axis=1, how="all")
        res[nom] = d
    return res


# ---------- Carrières (BS1, BS2, BS3, TG, KM, BA) ----------
CARRIERES = ["BS1", "BS2", "BS3", "TG", "KM", "BA"]
NON_CLASSEE = "Non classée"
FICHIER_META = os.path.join(DOSSIER, "carrieres.json")   # {nom_fichier: {carriere, ajoute}}


def charge_meta():
    try:
        with open(FICHIER_META, encoding="utf-8") as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def sauve_meta(d):
    tmp = FICHIER_META + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(d, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, FICHIER_META)


def detecte_carriere(nom):
    """Devine la carrière d'après le nom du fichier (None si absente ou ambiguë)."""
    n = nom.upper()
    trouvees = {"BS" + m.group(1)
                for m in re.finditer(r"(?<![A-Z0-9])BS\s*[-_.]?\s*([123])(?![0-9])", n)}
    for code in ("TG", "KM", "BA"):
        if re.search(rf"(?<![A-Z0-9]){code}(?![A-Z0-9])", n):
            trouvees.add(code)
    return next(iter(trouvees)) if len(trouvees) == 1 else None


def carriere_de(nom, meta=None):
    meta = charge_meta() if meta is None else meta
    c = (meta.get(nom) or {}).get("carriere")
    if c in CARRIERES:
        return c
    return detecte_carriere(nom) or NON_CLASSEE


def date_ajout(nom, meta=None):
    """Date d'enregistrement du fichier (sert à savoir quel fichier est le plus récent)."""
    meta = charge_meta() if meta is None else meta
    try:
        return float(meta[nom]["ajoute"])
    except Exception:
        try:
            return os.path.getmtime(os.path.join(DOSSIER, nom))
        except OSError:
            return 0.0


def cb_carriere(nom, cle_widget):
    v = st.session_state.get(cle_widget)
    m = charge_meta()
    if nom not in m:
        m[nom] = {"ajoute": date_ajout(nom, m)}
    m[nom]["carriere"] = v if v in CARRIERES else NON_CLASSEE
    sauve_meta(m)


def sauvegarde_zip():
    buf = io.BytesIO()
    meta = charge_meta()
    noms = liste_fichiers()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for nom in noms:
            z.write(os.path.join(DOSSIER, nom), arcname=nom)
        z.writestr("carrieres.json", json.dumps(
            {n: {"carriere": carriere_de(n, meta), "ajoute": date_ajout(n, meta)} for n in noms},
            ensure_ascii=False, indent=2))
        z.writestr("bons_commande.json", json.dumps(charge_bc(), ensure_ascii=False, indent=2))
    return buf.getvalue()


def restaure_zip(contenu):
    """Restaure les fichiers d'une sauvegarde ZIP (ceux déjà présents sont conservés)."""
    nb, ignores = 0, 0
    m = charge_meta()
    existants = set(liste_fichiers())
    with zipfile.ZipFile(io.BytesIO(contenu)) as z:
        try:
            info = json.loads(z.read("carrieres.json").decode("utf-8"))
        except Exception:
            info = {}
        for nom_zip in z.namelist():
            nom = nom_sur(nom_zip)
            if nom_zip.endswith("/") or not nom.lower().endswith(EXT):
                continue
            if nom in existants:
                ignores += 1
                continue
            with open(os.path.join(DOSSIER, nom), "wb") as fh:
                fh.write(z.read(nom_zip))
            mi = info.get(nom) or {}
            car = mi.get("carriere")
            try:
                ajoute = float(mi.get("ajoute"))
            except (TypeError, ValueError):
                ajoute = time.time()
            m[nom] = {"carriere": car if car in CARRIERES else (detecte_carriere(nom) or NON_CLASSEE),
                      "ajoute": ajoute}
            nb += 1
        nb_bc = 0
        try:
            bcs_zip = json.loads(z.read("bons_commande.json").decode("utf-8"))
        except Exception:
            bcs_zip = []
        if isinstance(bcs_zip, list) and bcs_zip:
            bcs_actuels = charge_bc()
            ids = {b.get("id") for b in bcs_actuels}
            for b in bcs_zip:
                try:
                    if isinstance(b, dict) and b.get("id") and b["id"] not in ids and b["ref"] and b["client"]:
                        bcs_actuels.append({
                            "id": str(b["id"]), "ref": str(b["ref"]), "client": str(b["client"]),
                            "chantier": str(b.get("chantier") or ""), "montant": float(b["montant"]),
                            "date_debut": str(b.get("date_debut") or ""), "date_fin": str(b.get("date_fin") or ""),
                            "clos": bool(b.get("clos")), "note": str(b.get("note") or ""),
                            "cree_par": str(b.get("cree_par") or ""), "cree_le": str(b.get("cree_le") or "")})
                        ids.add(b["id"])
                        nb_bc += 1
                except (KeyError, TypeError, ValueError):
                    continue
            sauve_bc(bcs_actuels)
    sauve_meta(m)
    return nb, ignores, nb_bc


# ---------- Bons de commande clients ----------
FICHIER_BC = os.path.join(DOSSIER, "bons_commande.json")
TOUS = "(tous les chantiers)"
STATUT_OK, STATUT_PROCHE = "🟢 OK", "🟡 Alerte (seuil atteint)"
STATUT_DEPASSE, STATUT_CLOS = "🔴 100 % atteint ou dépassé", "⚪ Clôturé"
COULEURS_STATUT = {STATUT_DEPASSE: "background-color: #f8d7da; color: #842029",
                   STATUT_PROCHE: "background-color: #fff3cd; color: #664d03"}


def charge_bc():
    try:
        with open(FICHIER_BC, encoding="utf-8") as fh:
            d = json.load(fh)
        return d if isinstance(d, list) else []
    except Exception:
        return []


def sauve_bc(liste):
    tmp = FICHIER_BC + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(liste, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, FICHIER_BC)


def date_iso(v):
    return v.isoformat() if hasattr(v, "isoformat") else ""


def _bc_valide(ref, client, montant, periode, d1, d2, autres, ignorer_id=None):
    """Renvoie un message d'erreur, ou None si les champs sont corrects."""
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
    """Tableau d'alertes : lignes jaunes (seuil) et rouges (100 %)."""
    d = d.drop(columns=["id"], errors="ignore")
    formats = {"Montant BC (HT net)": "{:,.2f}", "Livré (HT net)": "{:,.2f}", "Reste": "{:,.2f}",
               "Dépassement": "{:,.2f}", "% consommé": "{:.0f} %", "Qté livrée": "{:,.2f}"}
    try:
        return (d.style.apply(lambda r: [COULEURS_STATUT.get(r["Statut"], "")] * len(r), axis=1)
                .format({k: v for k, v in formats.items() if k in d.columns}, na_rep=""))
    except Exception:   # (jinja2 absent) : tableau sans couleurs
        return d


def _fr(iso):
    return f"{iso[8:10]}/{iso[5:7]}/{iso[0:4]}"


def calcule_situation(base, bcs, seuil):
    """Compare le montant livré (HT net) de chaque bon de commande à son montant."""
    colonnes = ["id", "Référence BC", "Client", "Chantier", "Période", "Montant BC (HT net)",
                "Livré (HT net)", "Reste", "Dépassement", "% consommé", "Statut", "Qté livrée"]
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
                       "Qté livrée": float(sub["Quantité"].sum()) if "Quantité" in sub.columns else None})
    return pd.DataFrame(lignes, columns=colonnes)


# =====================================================================
# 1) GESTION DES FICHIERS ENREGISTRÉS
# =====================================================================
for typ, texte in st.session_state.pop("msgs", []):
    getattr(st, typ)(texte)

cle = st.session_state["cle"]

# ---------- Gestion des utilisateurs (administrateur) ----------
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
        st.caption("Les comptes créés ici sont stockés sur le serveur : ils peuvent disparaître "
                   "au redémarrage de l'application. Téléchargez une sauvegarde de temps en "
                   "temps ; vous pourrez la restaurer en un clic.")
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

fichiers = liste_fichiers()

if est_admin:
    st.subheader("📁 Fichiers enregistrés sur la plateforme")
    meta = charge_meta()
    options_car = CARRIERES + [NON_CLASSEE]

    if fichiers:
        classes = sorted(fichiers, key=lambda x: (carriere_de(x, meta), x))
        for i, nom in enumerate(classes):
            chemin = os.path.join(DOSSIER, nom)
            c1, c2, c3, c4, c5 = st.columns([4.2, 1.5, 1.6, 1.7, 1.7])
            c1.markdown(f"**📄 {nom}**  \n<small>{info_fichier(chemin)}</small>",
                        unsafe_allow_html=True)
            actuelle = carriere_de(nom, meta)
            c2.selectbox("Carrière", options_car, index=options_car.index(actuelle),
                         key=f"car_{i}_{nom}", label_visibility="collapsed",
                         on_change=cb_carriere, args=(nom, f"car_{i}_{nom}"))
            with open(chemin, "rb") as fh:
                c3.download_button("⬇️ Télécharger", fh.read(), file_name=nom, key=f"dl_{i}")

            with c4.popover("✏️ Remplacer"):
                nouveau = st.file_uploader("Nouveau fichier Excel", type=TYPES, key=f"rep_{i}_{cle}")
                if nouveau is not None and st.button("Confirmer le remplacement", key=f"okrep_{i}"):
                    nouveau_nom = nom_sur(nouveau.name)
                    if nouveau_nom != nom and nouveau_nom in fichiers:
                        st.error("Un fichier portant ce nom existe déjà.")
                    else:
                        with open(os.path.join(DOSSIER, nouveau_nom), "wb") as fh:
                            fh.write(nouveau.getvalue())
                        if nouveau_nom != nom:
                            os.remove(chemin)
                        m = charge_meta()
                        car = carriere_de(nom, m)
                        if car == NON_CLASSEE:
                            car = detecte_carriere(nouveau_nom) or NON_CLASSEE
                        m.pop(nom, None)
                        m[nouveau_nom] = {"carriere": car, "ajoute": time.time()}
                        sauve_meta(m)
                        msg("success", f"« {nom} » a été remplacé par « {nouveau_nom} ».")
                        st.session_state["cle"] += 1
                        st.rerun()

            with c5.popover("🗑️ Supprimer"):
                st.write(f"Supprimer **{nom}** ?")
                if st.button("Oui, supprimer", key=f"del_{i}"):
                    os.remove(chemin)
                    m = charge_meta()
                    m.pop(nom, None)
                    sauve_meta(m)
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
        if ajouts and st.button("💾 Enregistrer sur la plateforme", type="primary"):
            existants = {empreinte_fichier(os.path.join(DOSSIER, f)): f for f in liste_fichiers()}
            m = charge_meta()
            for j, up in enumerate(ajouts):
                car = choix_car.get(j)
                contenu = up.getvalue()
                nom_up = nom_sur(up.name)
                h = empreinte_octets(contenu)
                if car not in CARRIERES:
                    msg("warning", f"« {up.name} » : choisissez sa carrière. Fichier non enregistré.")
                elif h in existants:
                    msg("warning", f"« {up.name} » est identique à « {existants[h]} » déjà "
                                   f"enregistré : ignoré (pas de doublon).")
                elif nom_up in existants.values():
                    msg("warning", f"Un fichier nommé « {nom_up} » existe déjà : utilisez "
                                   f"« Remplacer » pour le mettre à jour.")
                else:
                    with open(os.path.join(DOSSIER, nom_up), "wb") as fh:
                        fh.write(contenu)
                    m[nom_up] = {"carriere": car, "ajoute": time.time()}
                    existants[h] = nom_up
                    msg("success", f"« {nom_up} » enregistré ({car}).")
            sauve_meta(m)
            st.session_state["cle"] += 1
            st.rerun()

    with st.expander("💾 Sauvegarde et restauration (fichiers + bons de commande)"):
        st.caption("Les fichiers sont stockés sur le serveur : ils peuvent disparaître au "
                   "redémarrage de l'application. Téléchargez une sauvegarde régulièrement ; "
                   "elle contient aussi la carrière de chaque fichier.")
        if fichiers and st.button("📦 Préparer la sauvegarde (ZIP)"):
            st.session_state["zip_pret"] = sauvegarde_zip()
        if st.session_state.get("zip_pret"):
            st.download_button("⬇️ Télécharger la sauvegarde", st.session_state["zip_pret"],
                               file_name=f"sauvegarde_livraisons_{datetime.now():%Y-%m-%d}.zip",
                               mime="application/zip")
        zipup = st.file_uploader("Restaurer depuis une sauvegarde (ZIP)", type=["zip"],
                                 key=f"zip_{cle}")
        if zipup is not None and st.button("Restaurer la sauvegarde"):
            try:
                nb, ign, nbc = restaure_zip(zipup.getvalue())
                msg("success", f"{nb} fichier(s) restauré(s)"
                               + (f", {nbc} bon(s) de commande" if nbc else "")
                               + (f", {ign} déjà présent(s) ignoré(s)." if ign else "."))
            except Exception:
                msg("error", "Sauvegarde invalide.")
            st.session_state["zip_pret"] = None
            st.session_state["cle"] += 1
            st.rerun()

if not fichiers:
    if not est_admin:
        st.info("Aucun fichier disponible pour le moment. Contactez l'administrateur.")
    st.stop()

st.divider()

# =====================================================================
# 2) CHOIX DES FICHIERS / FEUILLES À ANALYSER
# =====================================================================
avance = st.sidebar.expander("⚙️ Fichiers et feuilles utilisés (avancé)")
sel_fichiers = avance.multiselect("Fichiers à analyser", fichiers, default=fichiers)
remplacer = avance.checkbox(
    "Un fichier plus récent remplace les mêmes jours (même carrière)", value=True,
    help="Pour une carrière, si un jour apparaît dans plusieurs fichiers (ex. rapport cumulé "
         "mis à jour chaque jour), seul le fichier enregistré en dernier est compté pour ce "
         "jour : pas de double comptage.")
meta_all = charge_meta()
if not sel_fichiers:
    st.warning("Sélectionnez au moins un fichier dans la barre latérale.")
    st.stop()

feuilles = {}   # label -> (fichier, feuille, df)
defaut, ignorees, vus = [], [], {}
for fch in sel_fichiers:
    chemin = os.path.join(DOSSIER, fch)
    try:
        classeur = lire_classeur(chemin, os.path.getmtime(chemin))
    except Exception as e:
        st.error(f"Impossible de lire « {fch} » : {e}")
        continue
    for feuille, d in classeur.items():
        label = f"{fch} › {feuille}"
        feuilles[label] = (fch, feuille, d)
        if d.empty:
            ignorees.append(f"{label} (vide)")
        elif any(m in feuille.lower() for m in MOTS_RECAP):
            ignorees.append(f"{label} (récapitulatif)")
        else:
            h = (carriere_de(fch, meta_all), empreinte_df(d))   # identique = même carrière + même contenu
            if h in vus:
                ignorees.append(f"{label} (identique à « {vus[h]} »)")
            else:
                vus[h] = label
                defaut.append(label)

if not feuilles:
    st.stop()

choisies = avance.multiselect("Feuilles incluses dans le calcul", list(feuilles), default=defaut)
if ignorees:
    with st.sidebar.expander(f"⚠️ {len(ignorees)} feuille(s) ignorée(s) par défaut"):
        st.caption("Récapitulatifs, feuilles vides ou identiques à une autre feuille : "
                   "elles feraient doubler les totaux. Ajoutez-les à la liste ci-dessus "
                   "si vous en avez besoin.")
        st.write("\n".join(f"- {x}" for x in ignorees))
if not choisies:
    st.warning("Sélectionnez au moins une feuille.")
    st.stop()

df = pd.concat([feuilles[l][2].assign(Fichier=feuilles[l][0], Feuille=feuilles[l][1],
                                      **{"Carrière": carriere_de(feuilles[l][0], meta_all)})
                for l in choisies], ignore_index=True)
cols = [c for c in df.columns if c not in ("Fichier", "Feuille", "Carrière")]


# ---------- Détection automatique des colonnes (silencieuse) ----------
def devine(mots):
    for m in mots:
        for c in cols:
            if m in c.lower():
                return c
    return None


def colonne(label, mots, requis=False):
    d = devine(mots)
    if d is None and requis:
        d = st.sidebar.selectbox(f"{label} (non détectée, à choisir)", [None] + cols,
                                 format_func=lambda x: "— choisir —" if x is None else x)
    return d


c_client = colonne("Client", ["client", "société", "societe", "raison", "destinataire",
                              "tiers", "chantier"], requis=True)
c_produit = colonne("Produit", ["produit", "désignation", "designation", "article", "matière",
                                "matiere", "nature"], requis=True)
c_date = colonne("Date", ["date"], requis=True)
c_qte = colonne("Quantité", ["qté en t", "qte en t", "tonnage", "quant", "qté", "qte", "poids"])
c_pu = colonne("Prix unitaire", ["p.u ht", "p.u", "prix", "pu"])
c_net = colonne("Montant HT Net", ["montant ht net", "ht net", "net ht", "montant ht",
                                   "total ht", "montant"])
c_chantier = colonne("Chantier", ["chantier", "destination", "lieu"])
if c_chantier is None:
    with st.sidebar.expander("🏗️ Colonne Chantier (non détectée)"):
        c_chantier = st.selectbox("Choisir la colonne Chantier", [None] + cols,
                                  format_func=lambda x: "— aucune —" if x is None else x)

if c_client is None or c_produit is None or c_date is None:
    st.warning("Certaines colonnes n'ont pas été détectées : choisissez-les dans la barre "
               "latérale. Voici les premières lignes lues :")
    st.dataframe(df.head(15), use_container_width=True)
    st.stop()

# ---------- Montant HT Net ----------
if c_net:
    df["Montant HT Net"] = to_num(df[c_net])
elif c_qte and c_pu:
    df["Montant HT Net"] = to_num(df[c_qte]) * to_num(df[c_pu])
else:
    st.error("Colonne « Montant HT » introuvable (ni Quantité + Prix unitaire). "
             "Vérifiez les noms de colonnes de votre fichier.")
    st.dataframe(df.head(15), use_container_width=True)
    st.stop()
df["Montant HT Net"] = df["Montant HT Net"].fillna(0)

# ---------- Nettoyage ----------
df["Client"] = texte_propre(df[c_client])
df["Produit"] = texte_propre(df[c_produit])
c_client, c_produit = "Client", "Produit"
df = df[~df[c_client].str.lower().isin(["", "nan", "none"])]
masque_total = (df[c_client].str.lower().str.contains("total")
                | df[c_produit].str.lower().str.contains("total"))
df = df[~masque_total]

if c_chantier:
    ch = texte_propre(df[c_chantier])
    df["Chantier"] = ch.where(~ch.str.lower().isin(["", "nan", "none", "nat"]), "(non renseigné)")

if c_qte:
    df["Quantité"] = to_num(df[c_qte]).fillna(0)

# ---------- Mois (à partir de la colonne date) ----------
d = pd.to_datetime(df[c_date], errors="coerce", dayfirst=True)
valide = d.notna()
# lignes sans date valide (ex. ligne de TOTAL en bas de feuille) : exclues du calcul
sans_date = df.loc[~valide].copy()
df = df.loc[valide].copy()
df["Mois"] = d.loc[valide].dt.strftime("%Y-%m")
df["Jour"] = d.loc[valide].dt.strftime("%Y-%m-%d")

# ---------- Consolidation : le fichier le plus récent remplace les mêmes jours d'une même carrière ----------
n_remplacees = 0
if remplacer and len(sel_fichiers) > 1:
    ordre = sorted(sel_fichiers, key=lambda x: (date_ajout(x, meta_all), x))
    df["_rang"] = df["Fichier"].map({fch: i for i, fch in enumerate(ordre)})
    dernier = df.groupby(["Carrière", "Jour"])["_rang"].transform("max")
    n_remplacees = int((df["_rang"] != dernier).sum())
    df = df[df["_rang"] == dernier].drop(columns="_rang")

if df.empty:
    st.warning("Aucune ligne exploitable après nettoyage. Vérifiez le fichier.")
    st.stop()

# ---------- Lignes en double ----------
colonnes_source = [c for c in cols if c in df.columns] + ["Carrière"]
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
    if c_qte:
        agg["Quantité"] = ("Quantité", "sum")
    verif = df.groupby(["Carrière", "Fichier", "Feuille", "Mois"]).agg(**agg).reset_index()
    st.dataframe(verif, use_container_width=True)
    if n_remplacees:
        st.info(f"{n_remplacees} ligne(s) ignorée(s) car un fichier plus récent de la même "
                f"carrière couvre les mêmes jours.")
    if len(sans_date):
        st.warning(f"{len(sans_date)} ligne(s) sans date valide ont été exclues du calcul "
                   f"(souvent la ligne de total du bas de la feuille) :")
        st.dataframe(sans_date, use_container_width=True)
    st.caption("Si un même mois apparaît dans deux fichiers (ex. août dans le fichier de "
               "septembre), ses livraisons sont comptées deux fois : retirez la feuille ou le "
               "fichier en trop. Comparez aussi chaque total avec la somme de la colonne "
               "Montant HT Net dans votre Excel.")

# ---------- Filtres (liés entre eux) ----------
FILTRES = {"f_carriere": "Carrière", "f_mois": "Mois", "f_client": c_client,
           "f_produit": c_produit}
if c_chantier:
    FILTRES["f_chantier"] = "Chantier"


def options_possibles(cle):
    """Valeurs encore possibles pour un filtre, compte tenu des AUTRES filtres choisis."""
    sub = df
    for k, col in FILTRES.items():
        choisi = st.session_state.get(k) or []
        if k != cle and choisi:
            sub = sub[sub[col].isin(choisi)]
    return sorted(sub[FILTRES[cle]].astype(str).unique(), key=str)


# retire les sélections devenues impossibles (ex. un produit qui n'appartient pas au client choisi)
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
sit = calcule_situation(df, bcs, seuil_alerte)
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
if c_qte:
    k4.metric("Quantité totale", fmt(f["Quantité"].sum()))

# ---------- Onglets ----------
MONTANT = "Montant HT Net"
QTE = "Quantité (m³)" if (c_qte and "m3" in c_qte.lower().replace("³", "3")) else "Quantité (T)"
indicateurs = [MONTANT] + ([QTE] if c_qte else [])
ind = (st.radio("Indicateur affiché dans les graphiques et tableaux croisés", indicateurs,
                horizontal=True) if c_qte else MONTANT)
col_ind = "Quantité" if ind == QTE else "Montant HT Net"


def resume(cle):
    """Montant HT Net, quantité et nombre de livraisons regroupés par `cle`."""
    agg = {MONTANT: ("Montant HT Net", "sum")}
    if c_qte:
        agg[QTE] = ("Quantité", "sum")
    agg["Livraisons"] = ("Montant HT Net", "size")
    return f.groupby(cle, as_index=False).agg(**agg)


def afficher(cle, croise=None):
    r = resume(cle)
    r = r.sort_values(cle) if cle in ("Mois", "Jour") else r.sort_values(ind, ascending=False)
    st.bar_chart(r, x=cle, y=ind)
    total = {cle: "TOTAL", **{c: r[c].sum() for c in r.columns if c != cle}}
    st.dataframe(pd.concat([r, pd.DataFrame([total])], ignore_index=True),
                 use_container_width=True, hide_index=True)
    if croise:
        st.markdown(f"**{cle} × {croise} — {ind}**")
        st.dataframe(f.pivot_table(index=cle, columns=croise, values=col_ind, aggfunc="sum",
                                   fill_value=0, margins=True, margins_name="Total"),
                     use_container_width=True)


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
        st.info("Aucun bon de commande enregistré : ajoutez-en dans l'onglet « Bons de commande ».")
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
               "client, toutes carrières confondues) est comparé au montant NET HT du bon.")
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

# ---------- Export ----------
buf = io.BytesIO()
with pd.ExcelWriter(buf, engine="openpyxl") as w:
    f.to_excel(w, sheet_name="Détail", index=False)
    f.groupby("Mois")["Montant HT Net"].sum().to_excel(w, sheet_name="Par mois")
    f.groupby(c_client)["Montant HT Net"].sum().to_excel(w, sheet_name="Par client")
    f.groupby(c_produit)["Montant HT Net"].sum().to_excel(w, sheet_name="Par produit")
st.download_button("⬇️ Exporter la sélection (Excel)", buf.getvalue(),
                   file_name="livraisons_filtrees.xlsx",
                   mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
