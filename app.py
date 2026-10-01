import io
import pandas as pd
import streamlit as st

st.set_page_config(page_title="Suivi livraisons carrières", page_icon="🚚", layout="wide")
st.title("🚚 Suivi des livraisons – Matériaux de carrière")

# ---------- Chargement du fichier ----------
fichier = st.file_uploader("Importer le fichier Excel de suivi", type=["xlsx", "xls", "xlsm"])
if fichier is None:
    st.info("Importez un fichier Excel pour commencer.")
    st.stop()

xl = pd.ExcelFile(fichier)
feuille = st.sidebar.selectbox("Feuille Excel", xl.sheet_names)
ligne_entete = st.sidebar.number_input("Ligne d'en-tête (0 = 1ère ligne)", 0, 50, 0)
df = xl.parse(feuille, header=int(ligne_entete))
df.columns = [str(c).strip() for c in df.columns]
df = df.dropna(how="all")


# ---------- Détection / choix des colonnes ----------
def devine(mots, cols):
    for c in cols:
        if any(m in c.lower() for m in mots):
            return c
    return None


cols = list(df.columns)
st.sidebar.subheader("Correspondance des colonnes")


def choix(label, mots, optionnel=False):
    options = ([None] if optionnel else []) + cols
    d = devine(mots, cols)
    idx = options.index(d) if d in options else 0
    return st.sidebar.selectbox(label, options, index=idx,
                                format_func=lambda x: "— aucune —" if x is None else x)


c_date = choix("Date", ["date"])
c_client = choix("Client", ["client"])
c_produit = choix("Produit", ["produit", "article", "matériau", "materiau", "désignation", "designation"])
c_qte = choix("Quantité", ["quant", "qté", "qte", "tonnage", "poids"], optionnel=True)
c_pu = choix("Prix unitaire", ["prix", "p.u", "pu "], optionnel=True)
c_net = choix("Montant HT Net", ["ht net", "net ht", "montant ht", "total ht"], optionnel=True)

# ---------- Préparation des données ----------
data = df.copy()
data[c_date] = pd.to_datetime(data[c_date], errors="coerce", dayfirst=True)
data = data.dropna(subset=[c_date])

if c_net:
    data["Montant HT Net"] = pd.to_numeric(data[c_net], errors="coerce").fillna(0)
elif c_qte and c_pu:
    data["Montant HT Net"] = (pd.to_numeric(data[c_qte], errors="coerce").fillna(0)
                              * pd.to_numeric(data[c_pu], errors="coerce").fillna(0))
    st.sidebar.caption("Montant HT Net calculé = Quantité × Prix unitaire")
else:
    st.error("Choisissez la colonne « Montant HT Net » ou bien Quantité + Prix unitaire.")
    st.stop()

data["Mois"] = data[c_date].dt.to_period("M").astype(str)
data[c_client] = data[c_client].astype(str).str.strip()
data[c_produit] = data[c_produit].astype(str).str.strip()
if c_qte:
    data["Quantité"] = pd.to_numeric(data[c_qte], errors="coerce").fillna(0)

# ---------- Filtres ----------
st.sidebar.subheader("Filtres")
mois = st.sidebar.multiselect("Mois", sorted(data["Mois"].unique()))
clients = st.sidebar.multiselect("Client", sorted(data[c_client].unique()))
produits = st.sidebar.multiselect("Produit", sorted(data[c_produit].unique()))

mn, mx = float(data["Montant HT Net"].min()), float(data["Montant HT Net"].max())
if mn < mx:
    plage = st.sidebar.slider("Montant HT Net (par ligne)", mn, mx, (mn, mx))
else:
    plage = (mn, mx)

f = data.copy()
if mois:
    f = f[f["Mois"].isin(mois)]
if clients:
    f = f[f[c_client].isin(clients)]
if produits:
    f = f[f[c_produit].isin(produits)]
f = f[f["Montant HT Net"].between(plage[0], plage[1])]

# ---------- Indicateurs ----------
k1, k2, k3, k4 = st.columns(4)
k1.metric("Montant HT Net total", f"{f['Montant HT Net'].sum():,.2f}".replace(",", " "))
k2.metric("Nb de livraisons", f"{len(f):,}".replace(",", " "))
k3.metric("Nb de clients", f[c_client].nunique())
if c_qte:
    k4.metric("Quantité totale", f"{f['Quantité'].sum():,.2f}".replace(",", " "))

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
    st.dataframe(f.drop(columns=["Mois"]), use_container_width=True)

# ---------- Export ----------
buf = io.BytesIO()
with pd.ExcelWriter(buf, engine="openpyxl") as w:
    f.drop(columns=["Mois"]).to_excel(w, sheet_name="Détail", index=False)
    f.groupby("Mois")["Montant HT Net"].sum().to_excel(w, sheet_name="Par mois")
    f.groupby(c_client)["Montant HT Net"].sum().to_excel(w, sheet_name="Par client")
    f.groupby(c_produit)["Montant HT Net"].sum().to_excel(w, sheet_name="Par produit")
st.download_button("⬇️ Exporter la sélection (Excel)", buf.getvalue(),
                   file_name="livraisons_filtrees.xlsx",
                   mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
