from datetime import datetime
import pandas as pd
from supabase import create_client
import streamlit as st

# Connexion à Supabase
supabase_url = st.secrets["supabase"]["url"]
supabase_key = st.secrets["supabase"]["key"]
supabase = create_client(supabase_url, supabase_key)

st.title("🏗️ Suivi des Livraisons - Carrières")

carriere_selectionnee = st.selectbox(
    "Sélectionnez la Carrière",
    ["BS", "Carrière 2", "Carrière 3", "Carrière 4", "Carrière 5"],
)

uploaded_file = st.file_uploader(
    "Glissez-déposez le fichier Excel complet (avec ses 33 feuilles)",
    type=["xlsx", "xls"],
)

if uploaded_file is not None:
  try:
    toutes_les_feuilles = pd.read_excel(uploaded_file, sheet_name=None)
    st.success(
        f"Fichier chargé avec succès ! {len(toutes_les_feuilles)} feuilles"
        " détectées."
    )

    # Afficher un aperçu de la première feuille pour vérifier les colonnes
    premier_nom = list(toutes_les_feuilles.keys())[0]
    df_test = pd.read_excel(uploaded_file, sheet_name=premier_nom, header=3)
    st.write(f"Aperçu des colonnes détectées sur la feuille '{premier_nom}' :")
    st.write(df_test.columns.tolist())
    st.dataframe(df_test.head(3))

    if st.button("Valider et envoyer tout vers Supabase"):
      total_insered = 0
      bar = st.progress(0)
      total_feuilles = len(toutes_les_feuilles)

      i = 0
      for nom_feuille, df in toutes_les_feuilles.items():
        # Lecture de la feuille avec header=3
        df_propre = pd.read_excel(uploaded_file, sheet_name=nom_feuille, header=3)

        records_a_inserer = []
        for index, row in df_propre.iterrows():
          # On prend la première colonne disponible comme date et la deuxième comme client
          # pour être sûr de ne rien rater même si les noms de colonnes varient un peu
          valeurs = row.values
          if len(valeurs) > 2 and pd.notna(valeurs[0]):
            record = {
                "carriere": carriere_selectionnee,
                "mois_annee": nom_feuille,
                "date_livraison": str(valeurs[0])[:10],
                "client": str(valeurs[1]) if pd.notna(valeurs[1]) else "",
                "produit": str(valeurs[2]) if len(valeurs) > 2 and pd.notna(valeurs[2]) else "",
                "qte_tonnes": float(valeurs[3]) if len(valeurs) > 3 and pd.notna(valeurs[3]) and isinstance(valeurs[3], (int, float)) else 0,
                "qte_m3": float(valeurs[4]) if len(valeurs) > 4 and pd.notna(valeurs[4]) and isinstance(valeurs[4], (int, float)) else 0,
                "montant_ht": float(valeurs[7]) if len(valeurs) > 7 and pd.notna(valeurs[7]) and isinstance(valeurs[7], (int, float)) else 0,
                "chantier": str(valeurs[10]) if len(valeurs) > 10 and pd.notna(valeurs[10]) else "",
            }
            records_a_inserer.append(record)

        if records_a_inserer:
          supabase.table("livraisons_carrieres").insert(
              records_a_inserer
          ).execute()
          total_insered += len(records_a_inserer)

        i += 1
        bar.progress(i / total_feuilles)

      st.success(
          f"Terminé ! Un total de {total_insered} lignes ont été importées avec"
          " succès."
      )

  except Exception as e:
    st.error(f"Une erreur est survenue : {e}")
