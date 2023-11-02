from pathlib import Path
import sys

import polars as pl
import altair as alt
import streamlit as st
from sqlalchemy import select

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import crud, models
from backend.config import DFType, Settings
from backend.db_tools import DBEngine

db_engine = DBEngine(**Settings().model_dump())


@st.cache_data
def get_bevolking_landelijk():
    stmt = (
        select(
            models.Bevolking.bevolking_1_januari,
            models.Geslacht.geslacht,
            models.Regios.regio,
            models.CategoryGroup.catgroup,
            models.Burgstaat.burgerlijkestaat,
            models.Perioden.jaar,
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
        .filter(models.Regios.regio_key == "NL01  ")
        .filter(models.CategoryGroup.catgroup == "Totaal")
        .filter(models.Burgstaat.burgerlijkestaat == "Totaal burgerlijke staat")
    )

    df = crud.fetch_data(stmt=stmt, db_engine=db_engine, package=DFType.POLARS)
    return df

@st.cache_data
def get_bodemgebruik_landelijk():
    """get_data_gemeentes_bodemgebruik _summary_

    Returns:
        _type_: _description_
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
        .filter(models.Regios.regio_key == "NL01  ")
        .filter(models.CategoryGroup.catgroup == "Totaal")
        .filter(models.Burgstaat.burgerlijkestaat == "Totaal burgerlijke staat")
    )

    df = crud.fetch_data(stmt=stmt, db_engine=db_engine, package=DFType.POLARS)
    df = df.drop(["id", "regio_key", "datum_key"])
    return df

def divide_columns_by_column(
    df: pl.DataFrame, divide_by_column: str, columns_to_exclude: list[str]
) -> pl.DataFrame:
    # Get a list of column names except the list to exclude
    columns_to_exclude.append(divide_by_column)
    columns_to_divide = [col for col in df.columns if col not in columns_to_exclude]

    # Iterate through the columns and divide by the specified column
    for column in columns_to_divide:
        df = df.with_columns(
            (df[column] / df[divide_by_column]).alias(f"{column}_relative")
        )

    return df

def main():
    st.set_page_config(
        page_title="Nederland",
    )

    st.markdown(
        """
        ## Nederland
        In dit tabblad wordt er gekeken naar statistieken omtrent bevolkingsgroei en bodemgebruik in Nederland, van het jaar 1988 tot en met 2023.
        """
    )

    df = get_bevolking_landelijk()

    st.markdown(
        """
        ### Jaarlijkse totale groei Nederland
        De jaarlijkse totale groei van de bevolking in Nederland, van het jaar 1988 tot en met 2023, is als volgt:
        """
    )
    toggle_geslacht = st.toggle("Split op geslacht", value=False)

    if toggle_geslacht:
        df_geslacht = df.filter(pl.col("geslacht") != "Totaal mannen en vrouwen")
        st.bar_chart(
            data=df_geslacht.to_pandas(),
            x="jaar",
            y="bevolking_1_januari",
            color="geslacht",
            height=600,
            width=800,
        )
    else:
        df_geslacht = df.filter(pl.col("geslacht") == "Totaal mannen en vrouwen")
        st.bar_chart(
            data=df_geslacht.to_pandas(),
            x="jaar",
            y="bevolking_1_januari",
            height=600,
            width=800,
        )

    st.markdown(
        """
        ### Relatieve groei Nederland
        De relatieve groei van Nederland, van het jaar 1988 tot en met 2023, is als volgt:
        """
    )
    radio_rel_abs = st.radio(
        "Relatieve of absolute groei?",
        ("Relatief", "Absoluut"),
        label_visibility="hidden",
    )

    df_growth = df.filter(pl.col("geslacht") == "Totaal mannen en vrouwen")
    df_growth = df_growth.with_columns(
        (pl.col("bevolking_1_januari").shift(1))
        .over("regio")
        .alias("previous_year")
    )
    df_growth = df_growth.with_columns(
        (
            (pl.col("bevolking_1_januari") - pl.col("previous_year"))
            / pl.col("previous_year")
            * 100
        ).alias("relative_growth")
    )
    df_growth = df_growth.with_columns(
        (pl.col("bevolking_1_januari") - pl.col("previous_year")).alias(
            "absolute_growth"
        )
    )

    if radio_rel_abs == "Relatief":
        st.line_chart(
            data=df_growth.to_pandas(),
            x="jaar",
            y="relative_growth",
            height=600,
            width=800,
        )
    elif radio_rel_abs == "Absoluut":
        st.line_chart(
            data=df_growth.to_pandas(),
            x="jaar",
            y="absolute_growth",
            height=600,
            width=800,
        )
    
    st.markdown(
        """
        ## Bodemgebruik in Nederland
        Het bodemgebruik in Nederland is als volgt:
        """
    )
    df_bodemgebruik = get_bodemgebruik_landelijk()
    df_bodemgebruik = df_bodemgebruik.filter(pl.col("jaar") == pl.col("jaar").max())

    exclude_cols = [
        "regio",
        "jaar",
        "bevolking_1_januari",
        "geslacht",
        "catgroup",
        "burgerlijkestaat",
    ]
    df_divided = divide_columns_by_column(
        df_bodemgebruik,
        divide_by_column="totale_oppervlakte",
        columns_to_exclude=exclude_cols,
    )
    df_divided = df_divided[
        [s.name for s in df_divided if not (s.null_count() == df_divided.height)]
    ]
    df_divided = df_divided.filter(pl.col("jaar") == pl.col("jaar").max())

    toggle_subcats = st.toggle("Sub-categorieën", value=False)

    
    if toggle_subcats:
        df_distribution = df_divided.melt(
            id_vars="regio",
            value_name="relative_percentage",
            value_vars=[
                col
                for col in df_divided.columns
                if not (col.startswith("totaal_")) and (col.endswith("relative"))
            ],
        )

    else:
        df_distribution = df_divided.melt(
            id_vars="regio",
            value_name="relative_percentage",
            value_vars=[
                col
                for col in df_divided.columns
                if (col.startswith("totaal_")) and (col.endswith("relative"))
            ],
        )

    chart_stacked = (
        alt.Chart(df_distribution)
        .mark_bar()
        .encode(
            x=alt.X("regio:N", axis=alt.Axis(title="Groups")),
            y=alt.Y(
                "sum(relative_percentage):Q",
                axis=alt.Axis(format="%"),
                stack="normalize",
            ),
            color=alt.Color("variable:N", title="Categories"),
            order=alt.Order("relative_percentage:N", sort="ascending"),
        )
        .properties(title="Verdeling bodemgebruik per Gemeente", height=600, width=800)
    )
    st.altair_chart(chart_stacked, use_container_width=True)


if __name__ == "__main__":
    main()