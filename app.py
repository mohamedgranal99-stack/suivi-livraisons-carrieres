from datetime import datetime
import pandas as pd
from supabase import create_client
import streamlit as st

# Configuration de la page Streamlit
st.set_page_config(
    page_title="Suivi des Livraisons - Carrière", page_icon="🏗️", layout="wide"
)

# Connexion à Supabase
supabase_url = st.secrets["supabase"]["url"]
supabase_key = st.secrets["supabase"]["key"]
supabase = create_client(supabase_url, supabase_key)

st.title("🏗️ Tableau de Bord - Suivi des Livraisons")

# Menu latéral pour basculer entre le Dashboard et l'Importation
menu = st.sidebar.selectbox(
    "Navigation", ["📊 Tableau de Bord", "📥 Importer un nouveau fichier"]
)


# --- 1. FONCTION DE CHARGEMENT DES DONNÉES DEPUIS SUPABASE ---
@st.cache_data(ttl=60)
def charger_donnees():
  response = (
      supabase.table("livraisons_carrieres")
      .select("*")
      .eq("carriere", "BS")
      .execute()
  )
  data = response.data
  if data:
    return pd.DataFrame(data)
  else:
    return pd.DataFrame()


df_global = charger_donnees()

# Bouton pratique pour vider le cache manuellement dans la barre latérale
if st.sidebar.button("🔄 Rafraîchir les données"):
  st.cache_data.clear()
  st.rerun()

# ==========================================
# PARTIE 1 : TABLEAU DE BORD (DASHBOARD)
# ==========================================
if menu == "📊 Tableau de Bord":
  if df_global.empty:
    st.warning(
        "Aucune donnée trouvée dans Supabase. Veuillez importer un fichier via"
        " le menu latéral."
    )
  else:
    st.sidebar.header("🔍 Filtres d'analyse")

    # Filtre par Mois / Année (Tous les mois, y compris les nouveaux, sont sélectionnés par défaut)
    mois_disponibles = sorted(df_global["mois_annee"].dropna().unique())
    mois_selectionnes = st.sidebar.multiselect(
        "Filtrer par Mois / Période",
        mois_disponibles,
        default=mois_disponibles,
    )

    # Filtre par Client
    clients_disponibles = sorted(df_global["client"].dropna().unique())
    client_selectionne = st.sidebar.multiselect(
        "Filtrer par Client", clients_disponibles
    )

    # Application des filtres
    df_Filtre = df_global[df_global["mois_annee"].isin(mois_selectionnes)]
    if client_selectionne:
      df_Filtre = df_Filtre[df_Filtre["client"].isin(client_selectionne)]

    # --- KPIs PRINCIPAUX ---
    st.subheader("📈 Indicateurs Clés de Performance (KPIs)")
    total_tonnes = df_Filtre["qte_tonnes"].sum()
    total_m3 = df_Fils_m3 = df_Filtre["qte_m3"].sum()
    total_montant = df_Filtre["montant_ht"].sum()

    col1, col2, col3 = st.columns(3)
    col1.metric("📦 Tonnage Total (T)", f"{total_tonnes:,.2f} T")
    col2.metric("📐 Volume Total (M3)", f"{total_m3:,.2f} m³")
    col3.metric("💰 Chiffre d'Affaires HT", f"{total_montant:,.2f} Dh")

    st.markdown("---")

    # --- GRAPHIQUES ET ANALYSES ---
    st.subheader("📊 Évolution des Livraisons en Tonnes")
    if not df_Filtre.empty:
      df_chart = (
          df_Filtre.groupby("mois_annee")["qte_tonnes"].sum().reset_index()
      )
      st.bar_chart(df_chart.set_index("mois_annee"))

    st.markdown("---")

    # --- TABLEAU DÉTAILLÉ ---
    st.subheader("📋 Détail des Livraisons")
    st.write(f"Affichage de {len(df_Filtre)} lignes filtrées :")
    st.dataframe(
        df_Filtre[
            [
                "mois_annee",
                "date_livraison",
                "client",
                "produit",
                "qte_tonnes",
                "qte_m3",
                "montant_ht",
                "chantier",
            ]
        ],
        use_container_width=True,
    )

# ==========================================
# PARTIE 2 : IMPORTATION DES FICHIERS
# ==========================================
elif menu == "📥 Importer un nouveau fichier":
  st.header("📥 Importer un classeur de livraisons")
  st.write(
      "Glissez-déposez le fichier Excel complet (avec ses 33 feuilles) pour"
      " actualiser la base de données."
  )

  uploaded_file = st.file_uploader(
      "Fichier Excel (.xlsx)", type=["xlsx", "xls"]
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
          df_propre = pd.read_excel(
              uploaded_file, sheet_name=nom_feuille, header=3
          )

          records_a_inserer = []
          for index, row in df_propre.iterrows():
            valeurs = row.values

            if len(valeurs) > 0 and pd.notna(valeurs[0]):
              date_str = str(valeurs[0]).strip()

              if date_str.startswith("202"):
                date_propre = date_str[:10]

                record = {
                    "carriere": "BS",
                    "mois_annee": nom_feuille,
                    "date_livraison": date_propre,
                    "client": (
                        str(valeurs[1])
                        if len(valeurs) > 1 and pd.notna(valeurs[1])
                        else ""
                    ),
                    "produit": (
                        str(valeurs[2])
                        if len(valeurs) > 2 and pd.notna(valeurs[2])
                        else ""
                    ),
                    "qte_tonnes": (
                        float(valeurs[3])
                        if len(valeurs) > 3
                        and pd.notna(valeurs[3])
                        and isinstance(valeurs[3], (int, float))
                        else 0
                    ),
                    "qte_m3": (
                        float(valeurs[4])
                        if len(valeurs) > 4
                        and pd.notna(valeurs[4])
                        and isinstance(valeurs[4], (int, float))
                        else 0
                    ),
                    "montant_ht": (
                        float(valeurs[7])
                        if len(valeurs) > 7
                        and pd.notna(valeurs[7])
                        and isinstance(valeurs[7], (int, float))
                        else 0
                    ),
                    "chantier": (
                        str(valeurs[10])
                        if len(valeurs) > 10 and pd.notna(valeurs[10])
                        else ""
                    ),
                }
                records_a_inserer.append(record)

          if records_a_inserer:
            try:
              supabase.table("livraisons_carrieres").insert(
                  records_a_inserer
              ).execute()
              total_insered += len(records_a_inserer)
            except Exception as db_err:
              st.warning(f"Erreur sur la feuille {nom_feuille}: {db_err}")

          i += 1
          bar.progress(i / total_feuilles)

        # IMPORTANT : On vide le cache automatiquement après l'import pour forcer la mise à jour
        st.cache_data.clear()

        st.success(
            f"Terminé ! {total_insered} lignes importées. Le cache a été"
            " nettoyé, retournez sur le 'Tableau de Bord'."
        )

    except Exception as e:
      st.error(f"Une erreur est survenue : {e}")
