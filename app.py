from datetime import datetime
import pandas as pd
from supabase import create_client
import streamlit as st

# Connexion à Supabase en utilisant les secrets de Streamlit Cloud
supabase_url = st.secrets["supabase"]["url"]
supabase_key = st.secrets["supabase"]["key"]
supabase = create_client(supabase_url, supabase_key)

st.title("🏗️ Suivi des Livraisons - Carrières")
st.write(
    "Importez le rapport mensuel de livraison pour alimenter la base de"
    " données en ligne."
)

# 1. Sélection de la carrière
carriere_selectionnee = st.selectbox(
    "Sélectionnez la Carrière",
    ["BS", "Carrière 2", "Carrière 3", "Carrière 4", "Carrière 5"],
)

# 2. Saisie du mois concerné
mois_concerne = st.text_input(
    "Mois et Année de la livraison (ex: SEPTEMBRE-2026)"
)

# 3. Upload du fichier Excel du mois
uploaded_file = st.file_uploader(
    "Glissez-déposez le fichier Excel du mois", type=["xlsx", "xls"]
)

if uploaded_file is not None and mois_concerne:
  try:
    # Lecture du fichier Excel
    df = pd.read_excel(uploaded_file)

    st.write("Aperçu des données lues dans le fichier :")
    st.dataframe(df.head())

    if st.button("Valider et envoyer vers Supabase"):
      records_a_inserer = []
      for index, row in df.iterrows():
        # Adaptation selon les colonnes de votre Excel
        record = {
            "carriere": carriere_selectionnee,
            "mois_annee": mois_concerne,
            "date_livraison": (
                str(row.get("Date"))[:10]
                if pd.notna(row.get("Date"))
                else None
            ),
            "client": str(row.get("Client")),
            "produit": str(row.get("Produit")),
            "qte_tonnes": (
                float(row.get("Qté en T"))
                if pd.notna(row.get("Qté en T"))
                else 0
            ),
            "qte_m3": (
                float(row.get("Qté en M3"))
                if pd.notna(row.get("Qté en M3"))
                else 0
            ),
            "montant_ht": (
                float(row.get("Montant HT"))
                if pd.notna(row.get("Montant HT"))
                else 0
            ),
            "chantier": str(row.get("Chantier")),
        }
        if record["date_livraison"] and record["client"] != "nan":
          records_a_inserer.append(record)

      # Envoi vers la table Supabase
      if records_a_inserer:
        response = (
            supabase.table("livraisons_carrieres")
            .insert(records_a_inserer)
            .execute()
        )
        st.success(
            f"Succès ! {len(records_a_inserer)} lignes importées pour la"
            f" {carriere_selectionnee} ({mois_concerne})."
        )
      else:
        st.warning("Aucune ligne valide trouvée à insérer.")

  except Exception as e:
    st.error(f"Une erreur est survenue : {e}")
