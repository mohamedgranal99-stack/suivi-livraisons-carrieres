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
    options = ([None] if optionnel else []) + cols
    d = devine(mots)
    idx = options.index(d) if d in options else 0
    return st.sidebar.selectbox(label, options, index=idx,
                                format_func=lambda x: "— aucune —" if x is None else x)


st.sidebar.subheader("Correspondance des colonnes")
c_client = choix("Client", ["client"])
c_produit = choix("Produit", ["produit", "désignation", "designation", "article", "matière",
                              "matiere", "nature"])
c_date = choix("Date", ["date"], optionnel=True)
c_qte = choix("Quantité", ["quant", "qté", "qte", "tonnage", "poids"], optionnel=True)
c_pu = choix("Prix unitaire", ["prix", "p.u", "p.u.", "pu"], optionnel=True)
c_net = choix("Montant HT Net", ["ht net", "net ht", "montant ht", "total ht", "montant"],
              optionnel=True)

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
df[c_client] = df[c_client].astype(str).str.strip()
df[c_produit] = df[c_produit].astype(str).str.strip()
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
src = st.sidebar.radio("Mois calculé à parti
