from datetime import datetime
import pandas as pd
from supabase import create_client
import streamlit as st

# Connexion à Supabase
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

# 2. Upload du fichier Excel du mois
uploaded_file = st.file_uploader(
    "Glissez-déposez le fichier Excel complet (avec ses 33 feuilles)",
    type=["xlsx", "xls"],
)

if uploaded_file is not None:
  try:
    # Lecture de TOUTES les feuilles du classeur Excel d'un coup
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
        # Ajustement : les en-têtes réels sont à la ligne 3 (header=3)
        # On recharge proprement la feuille avec le bon en-tête
        # (On peut relire la feuille depuis le fichier en sautant les 3 premières lignes)
        df_propre = pd.read_excel(uploaded_file, sheet_name=nom_feuille, header=3)

        records_a_inserer = []
        for index, row in df_propre.iterrows():
          date_val = row.get("Date")
          client_val = row.get("Client")

          # Vérifier que la ligne contient bien une date et un client valides
          if pd.notna(date_val) and pd.notna(client_val) and client_val != "nan":
            # Nettoyage de la date
            date_str = str(date_val)[:10]

            record = {
                "carriere": carriere_selectionnee,
                "mois_annee": nom_feuille,  # Utilise directement le nom de la feuille comme mois (ex: JANVIER-24, SEPT-26)
                "date_livraison": date_str,
                "client": str(client_val),
                "produit": (
                    str(row.get("Produit"))
                    if pd.notna(row.get("Produit"))
                    else ""
                ),
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
                "chantier": (
                    str(row.get("Chantier"))
                    if pd.notna(row.get("Chantier"))
                    else ""
                ),
            }
            records_a_inserer.append(record)

        # Insertion par lots dans Supabase si des lignes existent pour cette feuille
        if records_a_inserer:
          supabase.table("livraisons_carrieres").insert(
              records_a_inserer
          ).execute()
          total_insered += len(records_a_inserer)

        i += 1
        bar.progress(i / total_feuilles)

      st.success(
          f"Terminé ! Un total de {total_insered} lignes ont été importées pour"
          f" la {carriere_selectionnee} à travers toutes les feuilles."
      )

  except Exception as e:
    st.error(f"Une erreur est survenue : {e}")
