from pathlib import Path
import sys

import numpy as np
import polars as pl
import pandas as pd
import streamlit as st
from sklearn import linear_model, svm, tree, kernel_ridge
from sklearn.model_selection import train_test_split
from sqlalchemy import select

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import crud, models
from backend.config import DFType, Settings
from backend.db_tools import DBEngine

db_engine = DBEngine(**Settings().model_dump())


@st.cache_data
def get_data_gemeentes_bodemgebruik():
    """
    This function returns the data for the gemeentes joined with the bodemgebruik data, filtered on the following criteria:
    - Regio: Gemeentes
    - Geslacht: Totaal mannen en vrouwen
    - CategoryGroup: Totaal
    - Burgstaat: Totaal burgerlijke staat

    Returns:
        df: A polars dataframe containing the data for the gemeentes.
    """
    stmt = (
        select(
            models.Bevolking.bevolking_1_januari,
            models.Geslacht.geslacht,
            models.Regios.regio,
            models.CategoryGroup.catgroup,
            models.Burgstaat.burgerlijkestaat,
            models.Perioden.jaar,
            models.Bodemgebruik,
        )
        .join(
            models.Geslacht,
            models.Bevolking.geslacht_key == models.Geslacht.geslacht_key,
        )
        .join(models.Perioden, models.Bevolking.datum_key == models.Perioden.datum_key)
        .join(models.Regios, models.Bevolking.regio_key == models.Regios.regio_key)
        .join(
            models.Leeftijd,
            models.Bevolking.leeftijd_key == models.Leeftijd.leeftijd_key,
        )
        .join(
            models.CategoryGroup,
            models.Leeftijd.categorygroupid == models.CategoryGroup.catgroup_key,
        )
        .join(
            models.Burgstaat, models.Bevolking.burgst_key == models.Burgstaat.burgst_key
        )
        .join(
            models.Bodemgebruik,
            (models.Bevolking.regio_key == models.Bodemgebruik.regio_key)
            & (models.Bevolking.datum_key == models.Bodemgebruik.datum_key),
        )
        .filter(models.Regios.regio_key.startswith("GM"))
        .filter(models.CategoryGroup.catgroup == "Totaal")
        .filter(models.Burgstaat.burgerlijkestaat == "Totaal burgerlijke staat")
        .filter(models.Geslacht.geslacht == "Totaal mannen en vrouwen")
    )

    df = crud.fetch_data(stmt=stmt, db_engine=db_engine, package=DFType.POLARS)
    df = df.drop(["id", "regio_key", "datum_key"])
    return df


def growth_columns_by_year(
    df: pl.DataFrame, columns_to_exclude: list[str]
) -> pl.DataFrame:
    use_cols = [col for col in df.columns if col not in columns_to_exclude]

    for column in use_cols:
        df = df.with_columns(
            (pl.col(column).shift(1)).over("regio").alias(f"{column}_previous_moment")
        )
        df = df.with_columns(
            (
                (pl.col(column) - pl.col(f"{column}_previous_moment"))
                / pl.col(f"{column}_previous_moment")
            ).alias(f"{column}_growth")
        )
        df = df.fill_nan(0)

    # The following code is needed to replace inf values with 0, because of a bug in Polars.
    # We will replace them using pandas, and convert the dataframe back to polars before returning the dataframe
    df_pd = df.to_pandas()
    df_pd.replace([np.inf, -np.inf], 0, inplace=True)
    df = pl.from_pandas(df_pd)
    return df


def main():
    st.markdown(
        """
        ## Voorspellen van Bevolkingsgroei
        Om de bevolkingsgroei te voorspellen, wordt er gebruik gemaakt van verschillende soort modellen. De features die gebruikt worden zijn de reeds genoemde categorieën met een correlatie van boven de 0.5.
        De volgende modellen worden gebruikt:
        - Linear Regression
        - Support Vector Machine
        - Decision Tree Regressor
        - Kernel Ridge Regression

        In eerste instantie wordt de data opgesplitst in een test- en trainingsdataset met een verhouding van 80% trainingsdata en 20% testdata. Vervolgens worden de modellen getraind op de trainingsdata en wordt de score van het model berekend op de testdata. De score geeft aan hoe goed het model de testdata kan voorspellen. De score is een waarde tussen de 0 en 1, waarbij 1 betekent dat het model de testdata perfect kan voorspellen.
        De onderstaande tabs geven respectievelijk per model de score. Vervolgens wordt in een tabel de actual en predicted waardes naast elkaar gezet, zodat de voorspellingen van de modellen vergeleken kunnen worden met de werkelijke waardes.
        """
    )

    df_bodem = get_data_gemeentes_bodemgebruik()
    devdf_bodem = df_bodem.clone()

    regios = devdf_bevolking["regio"].to_list()
    exclude_cols = ["regio", "jaar", "geslacht", "catgroup", "burgerlijkestaat"]
    devdf_bodem = df_bodem.filter(df_bodem["regio"].is_in(regios))
    devdf_bodem = devdf_bodem.fill_null(strategy="zero")
    devdf_bodem = growth_columns_by_year(
        df=devdf_bodem, columns_to_exclude=exclude_cols
    )
    devdf_bodem = devdf_bodem[
        [s.name for s in devdf_bodem if not (s.null_count() == devdf_bodem.height)]
    ]
    devdf_bodem = devdf_bodem.drop_nulls("bevolking_1_januari_growth")

    devdf_bodem = devdf_bodem.select(
        [
            col
            for col in devdf_bodem.columns
            if (col in exclude_cols) or (col.endswith("growth"))
        ]
    )

    # Use a clone of the data for model training
    model_df = devdf_bodem.clone().to_pandas()

    # Split the data into train and test sets
    X = model_df[
        [
            col
            for col in model_df.columns
            if col not in ["bevolking_1_januari_growth", "jaar"]
        ]
    ]
    y = model_df["bevolking_1_januari_growth"]

    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2)

    linearModel, svmModel, decisiontreeModel, kernelridgeModel = st.tabs(
        [
            "Linear Regression",
            "Support Vector Machine",
            "Decision Tree Regressor",
            "Kernel Ridge Regression",
        ]
    )
    models = {
        "LinearRegression": linear_model.LinearRegression(),
        "SVM": svm.SVR(),
        "DecisionTreeRegressor": tree.DecisionTreeRegressor(),
        "KernelRidgeRegression": kernel_ridge.KernelRidge(),
    }
    trained_models = {}
    outcomes = {}
    # Fit all 4 models and print the scores in a tab
    for modelName, model in models.items():
        model.fit(X_train, y_train)
        trained_models[modelName] = model
        outcomes[modelName] = model.score(X_test, y_test)

    with linearModel:
        st.markdown(
            f"""
        ### Linear Regression
        De score van het lineare model is: {outcomes["LinearRegression"]}

        De parameters van dit model zijn als volgt: {trained_models["LinearRegression"].get_params()}
        
        """
        )

    with svmModel:
        st.markdown(
            f"""
        ### Support Vector Machine
        De score van het SVM model is: {outcomes["SVM"]}
        
        De parameters van dit model zijn als volgt: {trained_models["SVM"].get_params()}
        """
        )

    with decisiontreeModel:
        st.markdown(
            f"""
        ### Decision Tree Regressor
        De score van het Decision Tree Regressor model is: {outcomes["DecisionTreeRegressor"]}

        De parameters van dit model zijn als volgt: {trained_models["DecisionTreeRegressor"].get_params()}
        
        """
        )

    with kernelridgeModel:
        st.markdown(
            f"""
        ### Kernel Ridge Regression
        De score van het Kernel Ridge Regression model is: {outcomes["KernelRidgeRegression"]}

        """
        )

    # Create pandas dataframe to compare the predicted and actual values, with the actual and predicted columns next to each other
    df = pd.DataFrame()
    df["year"] = X_test.index
    df["actual"] = y_test.to_list()

    for modelName, model in trained_models.items():
        df[modelName] = model.predict(X_test)

    st.dataframe(df, use_container_width=True)

    # # create a scatter plot of the predicted and actual values
    # scatter_plot = alt.Chart(df).mark_point().encode(
    #     x=alt.X('year', scale=alt.Scale(domain=[1996, 2020])),
    #     y='value',
    #     color='type'
    # )

    # st.altair_chart(scatter_plot, use_container_width=True)


if __name__ == "__main__":
    main()