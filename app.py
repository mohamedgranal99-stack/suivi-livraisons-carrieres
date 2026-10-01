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

    if st.button("Valider et envoyer tout vers Supabase"):
      total_insered = 0
      bar = st.progress(0)
      total_feuilles = len(toutes_les_feuilles)

      i = 0
      for nom_feuille, df in toutes_les_feuilles.items():
        # On lit la feuille en sautant les 3 premières lignes (header=3)
        df_propre = pd.read_excel(uploaded_file, sheet_name=nom_feuille, header=3)

        records_a_inserer = []
        for index, row in df_propre.iterrows():
          valeurs = row.values

          # Vérification de sécurité : on s'assure qu'on a bien une date et que ce n'est pas le mot "Date"
          date_val = str(valeurs[0]) if len(valeurs) > 0 and pd.notna(valeurs[0]) else ""
          if date_val and "Date" not in date_val and "Unnamed" not in date_val:
            
            # Nettoyage sécurisé de la date (on prend les 10 premiers caractères ex: 2024-01-26)
            date_propre = date_val[:10] if len(date_val) >= 10 else None

            if date_propre:
              record = {
                  "carriere": carriere_selectionnee,
                  "mois_annee": nom_feuille,
                  "date_livraison": date_propre,
                  "client": str(valeurs[1]) if len(valeurs) > 1 and pd.notna(valeurs[1]) else "",
                  "produit": str(valeurs[2]) if len(valeurs) > 2 and pd.notna(valeurs[2]) else "",
                  "qte_tonnes": float(valeurs[3]) if len(valeurs) > 3 and pd.notna(valeurs[3]) and isinstance(valeurs[3], (int, float)) else 0,
                  "qte_m3": float(valeurs[4]) if len(valeurs) > 4 and pd.notna(valeurs[4]) and isinstance(valeurs[4], (int, float)) else 0,
                  "montant_ht": float(valeurs[7]) if len(valeurs) > 7 and pd.notna(valeurs[7]) and isinstance(valeurs[7], (int, float)) else 0,
                  "chantier": str(valeurs[10]) if len(valeurs) > 10 and pd.notna(valeurs[10]) else "",
              }
              records_a_inserer.append(record)

        # Insertion par lots dans Supabase pour cette feuille
        if records_a_inserer:
          try:
            supabase.table("livraisons_carrieres").insert(records_a_inserer).execute()
            total_insered += len(records_a_inserer)
          except Exception as db_err:
            st.warning(f"Erreur sur la feuille {nom_feuille}: {db_err}")

        i += 1
        bar.progress(i / total_feuilles)

      st.success(
          f"Terminé avec succès ! Un total de {total_insered} lignes ont été importées dans Supabase."
      )

  except Exception as e:
    st.error(f"Une erreur est survenue : {e}")
