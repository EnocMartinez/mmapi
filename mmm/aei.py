# !/usr/bin/env python3
"""
Gets data from Agencia Española de Investigación (https://www.aei.gob.es/).

WARNING: Their API is quite... limited to say the least. This may crash

author: Enoc Martínez
institution: Universitat Politècnica de Catalunya (UPC)
email: enoc.martinez@upc.edu
license: MIT
created: 3/10/23
"""


from .metadata_collector import MetadataCollector
import rich
import requests


def csv_with_multiple_lines(text):
    lines = text.split("\n")
    header = lines[0]
    body = lines[1]
    for line in lines[2:]:
        body += line

    return header, body


def get_aei_metadata(project_id: str) -> dict:
    """
    Returns project metadata based on data downloaded from AEI website. This script emulates a manual search, which
    returns a CSV file with only one file.
    :param project_id: AEI id
    :param institution: short name of the institution to look for more details, e.g. "UPC"
    :param clear: Clear all previously downloaded files
    :return: json structure with project data
    """
    url = (f"https://www.aei.gob.es/ayudas-concedidas/buscador-ayudas-concedidas/download/All/All/All/All?code="
           f"{project_id}&granted%5Bmin%5D=&granted%5Bmax%5D=")
    r = requests.get(url, verify=False)  # Yes, the spanish government does not have proper SSL certificate...
    text = r.text
    rich.print(text)
    try:
        header, value = text.split("\n")
    except ValueError:
        header, value = csv_with_multiple_lines(text)

    keys = header.replace("\"", "").split(";")
    values = value.replace("\"", "").split(";")
    aei_data = {}

    for i in range(len(keys)):
        aei_data[keys[i]] = values[i]

    for i in range(len(keys)):
        rich.print(f"[cyan] key: '{values[i]}'...", end="")
        if len(values[i]) == 0:
            continue
        if values[i][0] == "\"" == values[i][-1]:  # Remove redundant " chars '"hola"' -> 'hola'
            values[i] = values[i][1:-1]
            rich.print(f"[green]yes")
        else:
            rich.print(f"[yellow]no")
        aei_data[keys[i]] = values[i]

    rich.print(f"[purple]{aei_data.keys()}")
    data = {
        "#id": "",
        "title": aei_data["Título"],
        "startDate": "",
        "endDate": "",
        "acronym": "",
        "type": "national",
        "funding": {
            "grantId": project_id,
            "@organizations": "aei",
            "call": aei_data["Convocatoria"],
            "area": aei_data["Área"],
            "subarea": aei_data["Subárea"],
            "partners": []
        },
        "totalBudget": float(aei_data["€ Conced."].replace(".", "").replace(",", "."))
    }
    data["title"] = str(data["title"]).capitalize()
    return data


def aei_project(mc: MetadataCollector, project_id, acronym, time_start: str = "", time_end: str = "", force=False):
    """
    Creates a project from the data available at Agencia Estatal de Investigación
    :param project_id:
    :param acronym:
    :param time_start:
    :param time_end:
    :return:
    """

    data = get_aei_metadata(project_id)
    data["acronym"] = acronym
    data["#id"] = acronym
    return data
