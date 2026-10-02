import hashlib
import io
import os
import re
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


def msg(typ, texte):
    st.session_state.setdefault("msgs", []).append((typ, texte))


def to_num(s):
    if s.dtype == object:
        s = (s.astype(str).str.replace("\u00a0", "", regex=False).str.replace(" ", "", regex=False))
        # "2.719,36" -> 2719.36 ; "2719,36" -> 2719.36
        s = s.where(~s.str.contains(",", regex=False), s.str.replace(".", "", regex=False))
        s = s.str.replace(",", ".", regex=False)
    return pd.to_numeric(s, errors="coerce")


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

st.subheader("📁 Fichiers enregistrés sur la plateforme")
fichiers = liste_fichiers()
cle = st.session_state["cle"]

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
df["Client"] = df[c_client].astype(str).str.strip()
df["Produit"] = df[c_produit].astype(str).str.strip()
c_client, c_produit = "Client", "Produit"
df = df[~df[c_client].str.lower().isin(["", "nan", "none"])]
masque_total = (df[c_client].str.lower().str.contains("total")
                | df[c_produit].str.lower().str.contains("total"))
df = df[~masque_total]

if c_qte:
    df["Quantité"] = to_num(df[c_qte]).fillna(0)

# ---------- Mois (à partir de la colonne date) ----------
d = pd.to_datetime(df[c_date], errors="coerce", dayfirst=True)
df["Mois"] = d.dt.to_period("M").astype(str)
df = df[df["Mois"] != "NaT"]

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
    st.caption("Si un même mois apparaît dans deux fichiers (ex. août dans le fichier de "
               "septembre), ses livraisons sont comptées deux fois : retirez la feuille ou le "
               "fichier en trop. Comparez aussi chaque total avec la somme de la colonne "
               "Montant HT Net dans votre Excel.")

# ---------- Filtres ----------
st.sidebar.subheader("Filtres")
mois = st.sidebar.multiselect("Mois", sorted(df["Mois"].astype(str).unique(), key=str))
clients = st.sidebar.multiselect("Client", sorted(df[c_client].astype(str).unique(), key=str))
produits = st.sidebar.multiselect("Produit", sorted(df[c_produit].astype(str).unique(), key=str))

mn, mx = float(df["Montant HT Net"].min()), float(df["Montant HT Net"].max())
plage = st.sidebar.slider("Montant HT Net (par ligne)", mn, mx, (mn, mx)) if mn < mx else (mn, mx)

f = df.copy()
if mois:
    f = f[f["Mois"].isin(mois)]
if clients:
    f = f[f[c_client].isin(clients)]
if produits:
    f = f[f[c_produit].isin(produits)]
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
t1, t2, t3, t4 = st.tabs(["📅 Par mois", "👥 Par client", "🪨 Par produit", "📋 Détail"])

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
