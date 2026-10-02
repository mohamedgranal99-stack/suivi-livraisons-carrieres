import hashlib
import hmac
import io
import json
import os
import re
import secrets as pysecrets
import time
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

    if fichiers:
        for i, nom in enumerate(fichiers):
            chemin = os.path.join(DOSSIER, nom)
            c1, c2, c3, c4 = st.columns([5, 1.7, 1.8, 1.8])
            c1.markdown(f"**📄 {nom}**  \n<small>{info_fichier(chemin)}</small>",
                        unsafe_allow_html=True)
            with open(chemin, "rb") as fh:
                c2.download_button("⬇️ Télécharger", fh.read(), file_name=nom, key=f"dl_{i}")

            with c3.popover("✏️ Remplacer"):
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
                        msg("success", f"« {nom} » a été remplacé par « {nouveau_nom} ».")
                        st.session_state["cle"] += 1
                        st.rerun()

            with c4.popover("🗑️ Supprimer"):
                st.write(f"Supprimer **{nom}** ?")
                if st.button("Oui, supprimer", key=f"del_{i}"):
                    os.remove(chemin)
                    msg("success", f"« {nom} » a été supprimé.")
                    st.session_state["cle"] += 1
                    st.rerun()
    else:
        st.info("Aucun fichier enregistré. Ajoutez votre fichier Excel ci-dessous.")

    with st.expander("➕ Ajouter des fichiers", expanded=not fichiers):
        ajouts = st.file_uploader("Fichier(s) Excel de suivi", type=TYPES,
                                  accept_multiple_files=True, key=f"add_{cle}")
        if ajouts and st.button("💾 Enregistrer sur la plateforme", type="primary"):
            existants = {empreinte_fichier(os.path.join(DOSSIER, f)): f for f in liste_fichiers()}
            for up in ajouts:
                contenu = up.getvalue()
                nom_up = nom_sur(up.name)
                h = empreinte_octets(contenu)
                if h in existants:
                    msg("warning", f"« {up.name} » est identique à « {existants[h]} » déjà "
                                   f"enregistré : ignoré (pas de doublon).")
                elif nom_up in existants.values():
                    msg("warning", f"Un fichier nommé « {nom_up} » existe déjà : utilisez "
                                   f"« Remplacer » pour le mettre à jour.")
                else:
                    with open(os.path.join(DOSSIER, nom_up), "wb") as fh:
                        fh.write(contenu)
                    existants[h] = nom_up
                    msg("success", f"« {nom_up} » enregistré.")
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
sel_fichiers = st.sidebar.multiselect("Fichiers à analyser", fichiers, default=fichiers)
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
            h = empreinte_df(d)
            if h in vus:
                ignorees.append(f"{label} (identique à « {vus[h]} »)")
            else:
                vus[h] = label
                defaut.append(label)

if not feuilles:
    st.stop()

choisies = st.sidebar.multiselect("Feuilles incluses dans le calcul", list(feuilles), default=defaut)
if ignorees:
    with st.sidebar.expander(f"⚠️ {len(ignorees)} feuille(s) ignorée(s) par défaut"):
        st.caption("Récapitulatifs, feuilles vides ou identiques à une autre feuille : "
                   "elles feraient doubler les totaux. Ajoutez-les à la liste ci-dessus "
                   "si vous en avez besoin.")
        st.write("\n".join(f"- {x}" for x in ignorees))
if not choisies:
    st.warning("Sélectionnez au moins une feuille.")
    st.stop()

df = pd.concat([feuilles[l][2].assign(Fichier=feuilles[l][0], Feuille=feuilles[l][1])
                for l in choisies], ignore_index=True)
cols = [c for c in df.columns if c not in ("Fichier", "Feuille")]


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

if df.empty:
    st.warning("Aucune ligne exploitable après nettoyage. Vérifiez le fichier.")
    st.stop()

# ---------- Lignes en double ----------
colonnes_source = [c for c in cols if c in df.columns]
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
with st.expander("🔎 Vérification des totaux par fichier, feuille et mois"):
    agg = {"Lignes": ("Montant HT Net", "size"), "Montant_HT_Net": ("Montant HT Net", "sum")}
    if c_qte:
        agg["Quantité"] = ("Quantité", "sum")
    verif = df.groupby(["Fichier", "Feuille", "Mois"]).agg(**agg).reset_index()
    st.dataframe(verif, use_container_width=True)
    if len(sans_date):
        st.warning(f"{len(sans_date)} ligne(s) sans date valide ont été exclues du calcul "
                   f"(souvent la ligne de total du bas de la feuille) :")
        st.dataframe(sans_date, use_container_width=True)
    st.caption("Si un même mois apparaît dans deux fichiers (ex. août dans le fichier de "
               "septembre), ses livraisons sont comptées deux fois : retirez la feuille ou le "
               "fichier en trop. Comparez aussi chaque total avec la somme de la colonne "
               "Montant HT Net dans votre Excel.")

# ---------- Filtres (liés entre eux) ----------
FILTRES = {"f_mois": "Mois", "f_client": c_client, "f_produit": c_produit}
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
mois = st.sidebar.multiselect("Mois", options_possibles("f_mois"), key="f_mois")
clients = st.sidebar.multiselect("Client", options_possibles("f_client"), key="f_client")
produits = st.sidebar.multiselect("Produit", options_possibles("f_produit"), key="f_produit")
chantiers = (st.sidebar.multiselect("Chantier", options_possibles("f_chantier"), key="f_chantier")
             if c_chantier else [])

mn, mx = float(df["Montant HT Net"].min()), float(df["Montant HT Net"].max())
plage = st.sidebar.slider("Montant HT Net (par ligne)", mn, mx, (mn, mx)) if mn < mx else (mn, mx)

f = df.copy()
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


# ---------- Indicateurs ----------
k1, k2, k3, k4 = st.columns(4)
k1.metric("Montant HT Net total", fmt(f["Montant HT Net"].sum()))
k2.metric("Nb de lignes", f"{len(f):,}".replace(",", " "))
k3.metric("Nb de clients", f[c_client].nunique())
if c_qte:
    k4.metric("Quantité totale", fmt(f["Quantité"].sum()))

# ---------- Onglets ----------
noms_onglets = ["📅 Par mois", "👥 Par client", "🪨 Par produit"]
if c_chantier:
    noms_onglets.append("🏗️ Par chantier")
noms_onglets.append("📋 Détail")
onglets = st.tabs(noms_onglets)
t1, t2, t3, t4 = onglets[0], onglets[1], onglets[2], onglets[-1]

with t1:
    m = f.groupby("Mois", as_index=False)["Montant HT Net"].sum()
    st.bar_chart(m, x="Mois", y="Montant HT Net")
    st.dataframe(m, use_container_width=True)

with t2:
    c = (f.groupby(c_client, as_index=False)["Montant HT Net"].sum()
         .sort_values("Montant HT Net", ascending=False))
    st.bar_chart(c, x=c_client, y="Montant HT Net")
    st.dataframe(c, use_container_width=True)
    st.markdown("**Client × Mois**")
    st.dataframe(f.pivot_table(index=c_client, columns="Mois", values="Montant HT Net",
                               aggfunc="sum", fill_value=0, margins=True, margins_name="Total"),
                 use_container_width=True)

with t3:
    p = (f.groupby(c_produit, as_index=False)["Montant HT Net"].sum()
         .sort_values("Montant HT Net", ascending=False))
    st.bar_chart(p, x=c_produit, y="Montant HT Net")
    st.dataframe(p, use_container_width=True)
    st.markdown("**Produit × Client**")
    st.dataframe(f.pivot_table(index=c_produit, columns=c_client, values="Montant HT Net",
                               aggfunc="sum", fill_value=0, margins=True, margins_name="Total"),
                 use_container_width=True)

if c_chantier:
    with onglets[3]:
        h = (f.groupby("Chantier", as_index=False)["Montant HT Net"].sum()
             .sort_values("Montant HT Net", ascending=False))
        st.bar_chart(h, x="Chantier", y="Montant HT Net")
        st.dataframe(h, use_container_width=True)
        st.markdown("**Chantier × Mois**")
        st.dataframe(f.pivot_table(index="Chantier", columns="Mois", values="Montant HT Net",
                                   aggfunc="sum", fill_value=0, margins=True,
                                   margins_name="Total"), use_container_width=True)

with t4:
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
