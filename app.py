import io
import re
import pandas as pd
import streamlit as st

st.set_page_config(page_title="Suivi livraisons carrières", page_icon="🚚", layout="wide")
st.title("🚚 Suivi des livraisons – Matériaux de carrière")

MOTS_ENTETE = ["date", "client", "produit", "désignation", "designation", "quant", "qté", "qte",
               "prix", "p.u", "montant", "ht", "net", "tonnage", "matière", "matiere", "bl", "n°",
               "camion", "total", "tva", "ttc"]

MOIS_FR = {"janv": 1, "jan": 1, "fev": 2, "févr": 2, "fév": 2, "mars": 3, "avr": 4, "mai": 5,
           "juin": 6, "juil": 7, "aout": 8, "août": 8, "sept": 9, "oct": 10, "nov": 11,
           "dec": 12, "déc": 12}


def mois_depuis_feuille(nom):
    """'SEPT-26' -> '2026-09'. Renvoie None si non reconnu."""
    n = nom.lower().replace("é", "e").replace("û", "u")
    m = re.search(r"([a-z]+)\D*(\d{2,4})", n)
    if not m:
        return None
    txt, an = m.group(1), m.group(2)
    for k, v in MOIS_FR.items():
        if txt.startswith(k.replace("é", "e").replace("û", "u")):
            an = int(an)
            an = 2000 + an if an < 100 else an
            return f"{an}-{v:02d}"
    return None


def to_num(s):
    """Convertit en nombre (gère '1 234,50' et les textes)."""
    if s.dtype == object:
        s = (s.astype(str).str.replace("\u00a0", "", regex=False).str.replace(" ", "", regex=False)
             .str.replace(",", ".", regex=False))
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


# ---------- Chargement ----------
fichier = st.file_uploader("Importer le fichier Excel de suivi", type=["xlsx", "xls", "xlsm"])
if fichier is None:
    st.info("Importez un fichier Excel pour commencer.")
    st.stop()

xl = pd.ExcelFile(fichier)
feuilles = st.sidebar.multiselect("Feuilles à charger (ex. un mois par feuille)",
                                  xl.sheet_names, default=xl.sheet_names)
if not feuilles:
    st.warning("Sélectionnez au moins une feuille.")
    st.stop()

auto = st.sidebar.checkbox("Détecter automatiquement la ligne d'en-tête", value=True)
manuel = st.sidebar.number_input("Ligne d'en-tête manuelle (0 = 1ère ligne)", 0, 100, 0,
                                 disabled=auto)

frames, infos = [], []
for nom in feuilles:
    raw = xl.parse(nom, header=None)
    h = detecte_entete(raw) if auto else int(manuel)
    d = xl.parse(nom, header=h)
    d.columns = unique_cols(d.columns)
    d = d.dropna(how="all").dropna(axis=1, how="all")
    d["Feuille"] = nom
    d["Mois_feuille"] = mois_depuis_feuille(nom)
    frames.append(d)
    infos.append(f"{nom} → en-tête ligne {h}")

df = pd.concat(frames, ignore_index=True)
with st.sidebar.expander("En-têtes détectés"):
    st.write("\n".join(infos))

cols = [c for c in df.columns if c not in ("Feuille", "Mois_feuille")]

with st.expander("👀 Aperçu des données brutes", expanded=False):
    st.dataframe(df.head(30), use_container_width=True)


# ---------- Choix des colonnes ----------
def devine(mots):
    for c in cols:
        if any(m in c.lower() for m in mots):
            return c
    return None


def choix(label, mots, optionnel=False):
    options = [None] + cols
    d = devine(mots)
    idx = options.index(d) if d in options else 0
    return st.sidebar.selectbox(label, options, index=idx,
                                format_func=lambda x: "— aucune —" if x is None else x)


st.sidebar.subheader("Correspondance des colonnes")
c_client = choix("Client", ["client", "société", "societe", "raison", "destinataire", "tiers", "chantier"])
c_produit = choix("Produit", ["produit", "désignation", "designation", "article", "matière",
                              "matiere", "nature"])
c_date = choix("Date", ["date"], optionnel=True)
c_qte = choix("Quantité", ["quant", "qté", "qte", "tonnage", "poids"], optionnel=True)
c_pu = choix("Prix unitaire", ["prix", "p.u", "p.u.", "pu"], optionnel=True)
c_net = choix("Montant HT Net", ["ht net", "net ht", "montant ht", "total ht", "montant"],
              optionnel=True)

if c_client is None or c_produit is None:
    st.warning("Choisissez dans la barre latérale la colonne **Client** et la colonne **Produit** "
               "(non détectées automatiquement). Voici les premières lignes lues :")
    st.dataframe(df.head(15), use_container_width=True)
    st.stop()

# ---------- Montant HT Net ----------
if c_net:
    df["Montant HT Net"] = to_num(df[c_net])
elif c_qte and c_pu:
    df["Montant HT Net"] = to_num(df[c_qte]) * to_num(df[c_pu])
    st.sidebar.caption("Montant HT Net = Quantité × Prix unitaire")
else:
    st.error("Sélectionnez la colonne « Montant HT Net » (ou Quantité + Prix unitaire) "
             "dans la barre latérale. Vérifiez l'aperçu ci-dessus pour voir les noms de colonnes.")
    st.stop()

df["Montant HT Net"] = df["Montant HT Net"].fillna(0)

# ---------- Nettoyage ----------
# colonnes de travail internes (évite tout conflit de noms avec le fichier source)
df["Client"] = df[c_client].astype(str).str.strip()
df["Produit"] = df[c_produit].astype(str).str.strip()
c_client, c_produit = "Client", "Produit"
df = df[~df[c_client].str.lower().isin(["", "nan", "none"])]
# lignes de total / sous-total
masque_total = (df[c_client].str.lower().str.contains("total")
                | df[c_produit].str.lower().str.contains("total"))
df = df[~masque_total]

if c_qte:
    df["Quantité"] = to_num(df[c_qte]).fillna(0)

# ---------- Mois ----------
source = ["Nom de la feuille", "Colonne date"]
defaut = 0 if df["Mois_feuille"].notna().all() else 1
if not c_date:
    defaut = 0
src = st.sidebar.radio("Mois calculé à partir de", source, index=defaut)

if src == "Colonne date" and c_date:
    d = pd.to_datetime(df[c_date], errors="coerce", dayfirst=True)
    df["Mois"] = d.dt.to_period("M").astype(str)
    df = df[df["Mois"] != "NaT"]
else:
    df["Mois"] = df["Mois_feuille"].fillna(df["Feuille"])

if df.empty:
    st.warning("Aucune ligne exploitable après nettoyage. Vérifiez les colonnes choisies.")
    st.stop()

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
    st.dataframe(f.drop(columns=["Mois_feuille"]), use_container_width=True)

# ---------- Export ----------
buf = io.BytesIO()
with pd.ExcelWriter(buf, engine="openpyxl") as w:
    f.drop(columns=["Mois_feuille"]).to_excel(w, sheet_name="Détail", index=False)
    f.groupby("Mois")["Montant HT Net"].sum().to_excel(w, sheet_name="Par mois")
    f.groupby(c_client)["Montant HT Net"].sum().to_excel(w, sheet_name="Par client")
    f.groupby(c_produit)["Montant HT Net"].sum().to_excel(w, sheet_name="Par produit")
st.download_button("⬇️ Exporter la sélection (Excel)", buf.getvalue(),
                   file_name="livraisons_filtrees.xlsx",
                   mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
