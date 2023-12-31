import sys
import xml.etree.ElementTree as ET
from multiprocessing import Process, Value
from pathlib import Path
import numpy as np
from typing import Union
from sklearn.model_selection import train_test_split
from joblib import dump

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
import polars as pl
import requests
from loguru import logger

from backend.crud import upsert
from backend.db_tools import DBEngine
from backend.models import (
    Bevolking,
    Bodemgebruik,
    Burgstaat,
    CategoryGroup,
    Geslacht,
    Leeftijd,
    Perioden,
    Regios,
)


def parse_response_metadata(
    url: str,
    object: Union[Burgstaat, CategoryGroup, Geslacht, Leeftijd, Perioden, Regios],
) -> list[Union[Burgstaat, CategoryGroup, Geslacht, Leeftijd, Perioden, Regios]]:
    """Parse XML response from CBS Statline API."""
    row = {}
    lijst = []
    response = requests.get(url)
    response_xml = ET.fromstring(response.content)
    entries = response_xml.findall(".//{http://www.w3.org/2005/Atom}entry")
    for entry in entries:
        for key, value in object.__resp_keys__().items():
            param = find_in_schema(entry=entry, key=key)
            row[value] = param
        lijst.append(object(**row))
    return lijst


def parse_response_typed_dataset(
    chunk_size,
    object: Union[Bevolking, Bodemgebruik],
    url: str,
    total_rows_processed: Value,
) -> None:
    """Parse typed datasets XML response from CBS Statline API."""

    row = {}
    lijst = []
    logger.info(f"Parsing {object.__tablename__}...")
    save_dir = Path(f"data/parquet/{object.__tablename__}")
    save_dir.mkdir(parents=True, exist_ok=True)
    while True:
        with total_rows_processed.get_lock():
            skiprows = total_rows_processed.value
            total_rows_processed.value += chunk_size

        get_url = f"{url}?$skip={skiprows}"
        response = requests.get(get_url, stream=True)
        if response.status_code == 200:
            response_xml = ET.fromstring(response.content)
            entries = response_xml.findall(".//{http://www.w3.org/2005/Atom}entry")
            if len(entries) == 0:
                logger.info(f"All rows from {object.__tablename__} parsed.")
                break

            for entry in entries:
                for key, value in object.__resp_keys__().items():
                    param = find_in_schema(entry=entry, key=key)
                    row[value] = param
                row_dict = object(**row).__dict__
                row_dict.pop("_sa_instance_state")
                lijst.append(row_dict)

            skiprows += chunk_size
            df = pd.DataFrame.from_dict(lijst)
            df.to_parquet(
                f"data/parquet/{object.__tablename__}/{object.__tablename__.title()}_{skiprows}.parquet"
            )
            lijst = []
            logger.info(f"Parsed {skiprows} rows.")


def find_in_schema(entry: ET.Element, key: str) -> Union[str, None]:
    """Find key in schema."""
    schema = "{http://schemas.microsoft.com/ado/2007/08/dataservices}"
    param = entry.find(f".//{schema}{key}")
    if param is None:
        return None
    return param.text


def get_metadata_from_cbs(
    db_engine: DBEngine,
    models_dict: dict[
        str,
        Union[
            Burgstaat,
            Perioden,
            Bevolking,
            Bodemgebruik,
            Regios,
            CategoryGroup,
            Geslacht,
            Leeftijd,
        ],
    ],
) -> None:
    # Get data from CBS Statline API and upsert into database

    for key, value in models_dict.items():
        logger.info(f"Getting data from {key}...")
        data = parse_response_metadata(
            url=f"https://opendata.cbs.nl/ODataFeed/odata/03759ned/{key}", object=value
        )
        upsert(db_engine=db_engine, table=value, data=data)
        logger.info(f"Upserted {len(data)} records into {key}.")
    logger.info("Done!")


def get_data_from_cbs(
    object: Union[Bevolking, Bodemgebruik], url: str, num_processes: int = 4
):
    # Get data from CBS Statline API and save as parquet files due to the large size
    chunk_size = 10000  # Size of each data chunk

    # Create a list of processes
    processes = []
    logger.info(f"Starting {num_processes} processes...")
    total_rows_processed = Value("i", 0)
    for i in range(num_processes):
        # Create a new process and start it
        process = Process(
            target=parse_response_typed_dataset,
            args=(chunk_size, object, url, total_rows_processed),
        )
        processes.append(process)
        process.start()

    # Wait for all processes to finish
    for process in processes:
        process.join()


def parse_parquet_to_db(
    path: str,
    object: Union[Bevolking, Bodemgebruik],
    db_engine: DBEngine,
    regios: pl.Series,
):
    # Parse parquet files and upsert into database
    df = pl.read_parquet(path)
    df = df.cast(pl.Utf8)

    # Filter out rows that are not in the regio_key series
    df = df.filter(pl.col("regio_key").is_in(regios))

    list_of_dict = df.to_dicts()
    list_of_objects = [object(**row) for row in list_of_dict]

    upsert(db_engine=db_engine, table=object, data=list_of_objects)


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


def train_models(X, y, models: dict[str, object]) -> None:
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2)

    # Fit all 4 models and print the scores in a tab
    for modelName, model in models.items():
        model.fit(X_train, y_train)
        dump(model, f"backend/ml_models/{modelName}.joblib")
