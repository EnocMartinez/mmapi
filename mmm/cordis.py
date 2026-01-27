#!/usr/bin/env python3
"""
Gets data from cordis (https://cordis.europa.eu) and returns project info in JSON format

author: Enoc Martínez
institution: Universitat Politècnica de Catalunya (UPC)
email: enoc.martinez@upc.edu
license: MIT
created: 3/10/23
"""



import os.path
import lxml.etree as etree
import rich
import requests
from .xmlutils import get_elements, get_element, get_element_text
from .metadata_collector import MetadataCollector, init_metadata_collector, init_metadata_collector_env

# Harcoded acronyms that are usually missing in CORDIS
hardcoded_acronyms = {
    "CLEARWATER SENSORS LTD": "CWS",
    "MARINE INSTITUTE": "MI",
    "INSTITUT FRANCAIS DE RECHERCHE POUR L'EXPLOITATION DE LA MER": "ifremer",
    "ISTITUTO NAZIONALE DI GEOFISICA E VULCANOLOGIA": "INGV",
    "HELMHOLTZ-ZENTRUM FUR OZEANFORSCHUNG KIEL (GEOMAR)": "GEOMAR"
}


def assign_orgs_to_project(mc: MetadataCollector, data: dict) -> dict:
    """
    Loops through all the organizations in a project and assigns the proper @organizatino field (if found).
    If the shortName (acronym) OR one of the alternative names match it is considered the same
    """
    partners = []
    registered_organizations = mc.get_documents("organizations")
    for p in data["funding"]["partners"]:
        for org in registered_organizations:
            lower_alt_names = [n.lower() for n in org["alternativeNames"]]
            if p["acronym"].lower() == org["acronym"].lower() or p["fullName"].lower() in lower_alt_names:
                p["@organizations"] = org["#id"]
                break
        partners.append(p)
    data["funding"]["partners"] = partners
    return data


def get_cost_from_organization(organization: etree.ElementTree(), org_name: str):
    possible_terms = ["totalCost", "ecContribution", "netEcContribution"]
    try:
        return float(__attribute_from_names(organization, possible_terms))
    except LookupError:

        rich.print(f"[red]Cost could not be extracted from {org_name}, setting to 0 €[/red]")
        return 0.0


def __attribute_from_names(organization: etree.ElementTree, terms: list):
    """
    Checks for possible attributes in an etree element. Returns the value of the first element in the list found
    :param organization:  ElementTree containing a organization
    :param target:
    :return:
    """
    result = ""
    for term in terms:
        try:
           return organization.attrib[term]
        except KeyError:
            continue
    if not result:

        raise LookupError(f"Could not extract value, none of the following terms where found: {terms}")


def get_cordis_metadata(project_id: int, folder=".cordis", clear=False):
    """
    Returns project metadata based on data downloaded from CORDIS.
    :param project_id: cordis id
    :param institution: short name of the institution to look for more details, e.g. "UPC"
    :param clear: Clear all previously downloaded files
    :return: json structure with project data
    """

    os.makedirs(folder, exist_ok=True)
    if clear:
        rich.print("Cleaning downloaded files...")
        files = [os.path.join(folder, f) for f in os.listdir(folder)]
        for f in files:
            rich.print(f"deleting {f}")
            os.remove(f)

    tmp_file = os.path.join(folder, f"{project_id}.xml")
    if os.path.exists(tmp_file):
        rich.print("reading file locally")
    else:
        rich.print("file not found locally, downloading from cordis...", end="")
        url = f"https://cordis.europa.eu/project/id/{project_id}/en?format=xml"
        r = requests.get(url)
        with open(tmp_file, "w") as f:
            f.write(r.text)
        rich.print("[green]done")

    with open(tmp_file) as f:
        text = f.read()

    text = text.replace('<?xml version="1.0" encoding="UTF-8"?>', '')
    tree = etree.ElementTree(etree.fromstring(text))

    elements = { # key(cordis): value(output)
        "id": "project_id",
        "acronym": "acronym",
        "title": "title",
        "totalCost": "totalBudget",
        "startDate": "dateStart",
        "endDate": "dateEnd",
        #"objective": "summary"
    }
    data = {
        "#id": "",
        "type": "european"
    }
    pref = "{http://cordis.europa.eu}"
    for cordis_key, output_key in elements.items():
        data[output_key] = get_elements(tree, f"{pref}{cordis_key}")[0].text  # get always the first element

    data["#id"] = data["acronym"]  # use acronym as ID

    data["totalBudget"] = float(data["totalBudget"])
    data["funding"] = {
        "grantId": data.pop("project_id"),
        "@organizations": "ec"  # add European Comission as funder
    }

    # Getting call info, first try with relatedMasterCall
    try:
        call = get_element(tree, f"{pref}call", attr="type", attr_value="relatedMasterCall")
        data["funding"]["call"] = get_element_text(call, f"{pref}title")
    except LookupError:
        # then, go for subcall
        call = get_element(tree, f"{pref}call", attr="type", attr_value="relatedSubCall")
        data["funding"]["call"] = get_element_text(call, f"{pref}title")

    organizations = get_elements(tree, f"{pref}organization")
    partners_funding = []
    # Store information for ALL partners
    for o in organizations:
        org_funding = {}
        contribution = o.attrib["type"]
        org_funding["fullName"] = get_element_text(o, f"{pref}legalName")
        rich.print(f'[green]{org_funding["fullName"]}')
        try:
            org_funding["acronym"] = get_element_text(o, f"{pref}shortName")
        except LookupError as e:
            # If no acronym, let's try to find it in our hardcode list
            if org_funding["fullName"] in hardcoded_acronyms.keys():
                org_funding["acronym"] = hardcoded_acronyms[org_funding["fullName"]]
            else:
                org_funding["acronym"] = ""
                rich.print(f"[yellow]No shortName nor hardcoded acronym for '{org_funding['fullName']}'")

        org_funding["partnershipType"] = o.attrib["type"]
        org_funding["budget"] = get_cost_from_organization(o, org_funding["fullName"])
        org_funding["partnershipType"] = o.attrib["type"]

        partners_funding.append(org_funding)
    data["funding"]["partners"] = partners_funding
    return data