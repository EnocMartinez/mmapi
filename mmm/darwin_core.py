import datetime
import logging
import os
from zipfile import ZipFile
from lxml import etree
import pandas as pd
import requests

from mmm import MetadataCollector, SensorThingsApiDB
from mmm.common import assert_type, LoggerSuperclass, GRN
from mmm.data_sources.postgresql import sql_list
from mmm.xmlutils import get_element, serialize_xml, create_element


# Darwin Core Event Core + extended Measurement or Fact table (eMoF)
#
#
#
#                                         ┌────────────────────────────────────────────────────┐
#                                         │       Occurrence                                   │
#        events table                     ├─────────┬─────────────────────────┬────────────────┤
#  ┌────────────────────────────┐         │eventID  │  OccurrenceID           │ taxa           │
#  │  eventID                   │         ┼─────────┼─────────────────────────┼────────────────┼
#  ┼────────────────────────────┼   ┌─────►         │  https://.../pic1?taxa1 │ Chromis chromis────┐
#  │ https://mycamera.com/pic1 ─┼───┴─────►         │  https://.../pic1?taxa2 │ Coris julis    ┼───┼──┐
#  │ https://mycamera.com/pic2 ─┼─────────►         │  https://.../pic2?taxa1 │ Chromis chromis│   │  │
#  │                            │         └─────────┼─────────────────────────┼────────────────┘   │  │
#  └────────────────────────────┘                                                                  │  │
#                                                                                                  │  │
#                                                                                                  │  │
#                                                                                                  │  │
#                                    ┌───────────────────────────────────────────┐                 │  │
#                                    │    eMoF Table                             │                 │  │
#                                    │────────────────────┬──────────────────────│                 │  │
#                                    │measurementType     │  measurementValue    │                 │  │
#                                    ┼────────────────────┼──────────────────────┤                 │  │
#                                    │confidence          │  0.79                ◄─────────────────┼  │
#                                    │bounding_box_xyxy   │  0.14 0.78 0.45 0.69 ◄─────────────────┘  │
#                                    │confidence          │                      │                    │
#                                    │boundi    ng box_xyxy   │  0.76                ◄────────────────────┤
#                                    │confidence          │  0.45 0.78 0.36 0.95 ◄────────────────────┘
#                                    └────────────────────┼──────────────────────┘


class DarwinCoreArchive(LoggerSuperclass):
    """
    Creates a Darwin Core Archive from data in the database. The DataFrame must have the following columns:
    'timestamp' (index), 'depth', 'value', 'parameters', 'variable', 'sensor_id', 'platform_id','foi'

    It is expected that the detections are stored inside results as:
        [{"taxa": "Chromis chromis", "confidence": 0.975, "bounding_box_xyxy": [0.389, 0.312, 0.461, 0.427]}, {"taxa": "Chromis chromis", "confidence": 0.969, "bounding_box_xyxy": [0.816, 0.091, 0.836, 0.14]}, {"taxa": "Chromis chromis", "confidence": 0.927, "bounding_box_xyxy": [0.21, 0.69, 0.23, 0.724]}, {"taxa": "Chromis chromis", "confidence": 0.911, "bounding_box_xyxy": [0.524, 0, 0.547, 0.046]}, {"taxa": "Chromis chromis", "confidence": 0.86, "bounding_box_xyxy": [0.541, 0.311, 0.556, 0.339]}, {"taxa": "Chromis chromis", "confidence": 0.713, "bounding_box_xyxy": [0.381, 0.398, 0.395, 0.421]}, {"taxa": "Chromis chromis", "confidence": 0.656, "bounding_box_xyxy": [0.236, 0.402, 0.259, 0.422]}]

    """
    def __init__(self, mc: MetadataCollector, sta: SensorThingsApiDB, df: pd.DataFrame, dataset: dict,
                 time_start: pd.Timestamp, time_end: pd.Timestamp, log:logging.Logger):
        """
        Creates a Darwin Core class with Event core, Occurrences and eMoF tables
        :param mc:
        """

        LoggerSuperclass.__init__(self, log, "DwC", colour=GRN)
        self.dwc_prefix = "http://rs.tdwg.org/dwc/terms/"

        if os.path.exists(".species.cache"):
            self.__species_cache = pd.read_csv(".species.cache")
            self.__species_cache["aphiaID"] = self.__species_cache["aphiaID"].astype(str)
        else:
            self.__species_cache = pd.DataFrame(columns=["aphiaID", "taxonRankName", "taxonRankValue"])

        # Terms found on https://rs.obis.org/obis/terms
        self.ris_iobis_terms = ["measurementTypeID", "measurementValueID", "measurementUnitID"]
        self.ris_iobis_prefix = "http://rs.obis.org/obis/terms/"
        self.field_separator = "\\t"

        self.line_separator = "\\n"

        assert_type(mc, MetadataCollector)
        assert_type(sta, SensorThingsApiDB)
        self.mc = mc
        self.sta = sta
        self.dataset = dataset
        # Get the AI process
        self.process = self.mc.get_document("processes", dataset["constraints"]["@processes"])

        log.debug("Merging feature of interest descriptions into main dataframe")
        fois_ids = df["foi"].unique().tolist()
        foi_names = self.get_foi_description(fois_ids)
        df = df.reset_index()
        df = df.merge(foi_names, on="foi", how="left")
        df = df.set_index("timestamp")
        self.df = df

        # Files are empty by default
        self.f_events = ""
        self.f_occurrences = ""
        self.f_emofs = ""
        self.taxa_dict = {}

        sensors = [self.mc.get_document("sensors", s) for s in df["sensor_id"].unique().tolist()]

        assert len(dataset["@stations"]) == 1, f"Darwin Core only implemented for dataset with only one station"

        station_id = self.dataset["@stations"][0]
        station = self.mc.get_document("stations", station_id)
        station_name = station["#id"]
        process_name = self.process["#id"]
        if "reference" not in self.process.keys():
            self.error(f"reference field not included in process '{process_name}'", exception=ValueError)

        algorithm_reference = self.process["reference"]

        taxa_dict = self.mc.get_taxa_aphia_dict()
        self.taxa_dict = taxa_dict

        latitude, longitude, depth = self.mc.get_station_coordinates(station_name, time_start)
        self.latitude = latitude
        self.longitude = longitude
        self.station_name = station_name

        events = []
        occurrences = []
        emofs = []

        # Creating event hierarcy as follows
        # <dataset>_<from>_<to>
        #    │──camera1_<from>_<to>
        #    │  │── camera1_picture1
        #    │  └── camera1_picture2
        #    │
        #    └──camera2_<from>_<to>
        #       │── camera2_picture1
        #       └── camera2_picture2
        #

        self.info("Creating event hierarchy...")
        df["parentEventID"] = ""
        self.parent_event_id = dataset["#id"] + "_" + time_start.strftime("%Y%m%d") + "_" + time_end.strftime("%Y%m%d")
        self.debug(f"Parent event ID: {self.parent_event_id}")

        # Append parent event ID
        events.append({
            "id": self.parent_event_id,
            "eventID": self.parent_event_id,
            "parentEventID": "",
            "geodeticDatum": "EPSG:4326",
            "decimalLatitude": latitude,
            "decimalLongitude": longitude,
            "minimumDepthInMeters": depth,
            "maximumDepthInMeters": depth,
            "eventTime": time_start.strftime("%Y-%m-%dT%H:%M:%SZ") + "/" + time_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "datasetName": dataset["title"],
            "institutionCode": "UPC",  # TODO change this hardcoded institution!
        })

        self.camera_events = {}
        for sensor in sensors:
            camera_event_id = sensor["#id"] + "_" + time_start.strftime("%Y%m%d") + "_" + time_end.strftime("%Y%m%d")
            self.debug(f"Creating camera event ID: {camera_event_id}")
            events.append({
                "id": camera_event_id,
                "eventID": camera_event_id,
                "parentEventID": self.parent_event_id,
                "eventTime": time_start.strftime("%Y-%m-%dT%H:%M:%SZ") + "/" + time_end.strftime("%Y-%m-%dT%H:%M:%SZ")
            })
            df = self.df
            df.loc[df["sensor_id"] == sensor["#id"], "parentEventID"] = camera_event_id
            self.camera_events[sensor["#id"]] = camera_event_id


        #======== Station Metadata ========#
        self.info("Adding station metadata to EMOF table...")
        station_name = station["#id"]
        station_url = ""
        # Trying to extract OSO info
        try:
            station_name = station["oso"]["platform"]["label"]
            station_url = station["oso"]["platform"]["definition"]
            self.debug(f"Using OSO information, station name: '{station_name}'")
        except KeyError:
            self.debug("OSO info not found")
            pass

        emofs.append({
            "id": self.parent_event_id,
            "occurrenceID": "",
            "measurementType": "Name of sampling platform",
            "measurementTypeID": "http://vocab.nerc.ac.uk/collection/P01/current/NMSPINST/",
            "measurementValue": station_name,
            "measurementValueID": station_url,
            # measurementUnit
            # measurementUnitID  -> URL
            }
        )

        try:
            ptype_name = station["platformType"]["label"]
            ptype_url = station["platformType"]["definition"]
        except KeyError as e:
            self.error(f"Could not get platform type for station: '{station_id}'")
            self.logger.exception(e)
            raise e

        emofs.append({
            "id": self.parent_event_id,
            "occurrenceID": "",
            "measurementType": "Platform type",
            "measurementTypeID": "http://vocab.nerc.ac.uk/collection/W06/current/CLSS0001/",
            "measurementValue": ptype_name,
            "measurementValueID": ptype_url,
        })

        #======== Camera Metadata ========#
        self.info(f"Adding camera metadata to EMOF table...")
        for sensor in sensors:
            sensor_id = sensor["#id"]
            self.debug(f"    camera: {sensor_id}")
            camera_event = self.camera_events[sensor["#id"]]
            # append camera name
            emofs.append({
                "id": camera_event,
                "measurementType": "Name of sampling instrument",
                "measurementTypeID": "http://vocab.nerc.ac.uk/collection/P01/current/NMSPINST/",
                "measurementValue": sensor_id
            })
            # instrument type
            emofs.append({
                "id": camera_event,
                "measurementType": "Instrument type",
                "measurementTypeID": "https://vocab.nerc.ac.uk/collection/W06/current/CLSS0002/",
                "measurementValue": sensor["instrumentType"]["label"],
                "measurementValueID": sensor["instrumentType"]["definition"]
            })
            # instrument model
            emofs.append({
                "id": camera_event,
                "measurementType": "instrument model",
                "measurementTypeID": "",
                "measurementValue": sensor["model"]["label"],
                "measurementValueID": sensor["model"]["definition"]
            })

        for idx, row in df.iterrows():
            # Store the picture as an event
            pic = row["parameters"]["sourceImage"]

            events.append({
                "id": pic,
                "eventID": pic,
                "parentEventID": row["parentEventID"],
                "eventType": "Observation",
                "eventDate": idx.strftime("%Y-%m-%dT%H:%M:%SZ")
            })

            # Add FieldOfView for every
            emofs.append({
                "id": pic,
                "measurementType": "field of view",
                "measurementValue": row["foi"],
                "measurementRemarks": row["measurementRemarks"]
            })

            for i, res in enumerate(row["value"]):
                taxa = res["taxa"]
                occurrence_id = pic + f"?n={i + 1:03d}"
                if taxa not in taxa_dict.keys():
                    self.debug(f"Ignoring pic with taxa '{taxa}' {pic}")
                    continue
                occurrences.append({
                    "id": pic,
                    "occurrenceID": occurrence_id,
                    "scientificName": res["taxa"],
                    "scientificNameID": "urn:lsid:marinespecies.org:taxname:" + str(taxa_dict[taxa]),
                    "identificationReferences": algorithm_reference,
                    "basisOfRecord": "MachineObservation",
                    "identificationVerificationStatus": "PredictedByMachine",
                    "occurrenceStatus": "present",
                    "associatedMedia": pic,
                })
                bounding_box_xyxy = " ".join([str(f) for f in res["bounding_box_xyxy"]])

                emofs.append({
                    "id": pic,
                    "occurrenceID": occurrence_id,
                    "measurementType": "bounding_box_xyxy",
                    "measurementValue": bounding_box_xyxy,
                })

                emofs.append({
                    "id": pic,
                    "occurrenceID": occurrence_id,
                    "measurementType": "confidence",
                    "measurementValue": res["confidence"],
                    "measurementUnit": "fraction of 1",
                    "measurementUnitID": "https://vocab.nerc.ac.uk/collection/P06/current/UUUU/"

                })

        self.emofs = pd.DataFrame(emofs)
        self.occurrences = pd.DataFrame(occurrences)
        self.events = pd.DataFrame(events)
        self.emofs.index.name = "index"
        self.occurrences.index.name = "index"
        self.events.index.name = "index"


    def __repr__(self):
        return "\n".join([
            "==== Darwin Core Archive ====",
            f"station: '{self.station['#id']}'",
            f"sensor: '{self.sensor['#id']}'",
            f"fieldOfView: '{self.field_of_view}'",
            f"time coverage: from {self.time_start.strftime('%Y:%m:%d %H:%M:%S')} to {self.time_end.strftime('%Y:%m:%d %H:%M:%S')}",
            f"-----------------------------",
            f"Event table rows {len(self.events)}",
            f"Occurrences table rows {len(self.occurrences)}",
            f"eMoF table rows: {len(self.emofs)}",
            f"-----------------------------",
            f"Species detected: {len(self.occurrences['scientificNameID'].unique())}",
            f"============================="
        ])

    def get_foi_description(self, foi_ids):
        names = sql_list(foi_ids, string=True)
        q = f"""
        select "NAME" as foi, "DESCRIPTION" as "measurementRemarks" from "FEATURES" where "NAME" in {names}
        """
        return self.sta.dataframe_from_query(q)

    def create_archive(self, filename):
        f"""
        Creates a ZIP archive for Darwin Core Archive with Event, Occurrence and eMoF tables and eml.xml and meta.xml files
        """

        
        tmp_folder = os.path.dirname(filename)
        os.makedirs(tmp_folder, exist_ok=True)
        self.f_events = os.path.join(tmp_folder, "events.txt")
        self.f_occurrences = os.path.join(tmp_folder, "occurrences.txt")
        self.f_emofs = os.path.join(tmp_folder, "emofs.txt")
        self.events.to_csv(self.f_events, index=False, sep="\t", encoding="utf-8")
        self.occurrences.to_csv(self.f_occurrences, index=False, sep="\t", encoding="utf-8")
        self.emofs.to_csv(self.f_emofs, index=False, sep="\t", encoding="utf-8")
        
        self.meta_xml = self.create_meta("meta.xml", tmp_folder)
        self.eml_xml = self.create_eml("eml.xml", tmp_folder)
        if not filename.endswith(".zip"):
            filename += ".zip"

        self.info(f"Creating a ZIP file: {filename}")
        with ZipFile(filename, 'w') as myzip:
            for filename in [self.f_events, self.f_occurrences, self.f_emofs, self.meta_xml, self.eml_xml]:
                myzip.write(filename, os.path.basename(filename))
        return filename


    def create_meta(self, filename, folder):
        # Now, let's create the XML metadata file
        meta_xml = f"""
        <archive xmlns="http://rs.tdwg.org/dwc/text/" metadata="eml.xml">
          <core encoding="UTF-8" fieldsTerminatedBy="\\t" linesTerminatedBy="\\n" fieldsEnclosedBy="" ignoreHeaderLines="1" rowType="http://rs.tdwg.org/dwc/terms/Event">
            <files>
              <location>{os.path.basename(self.f_events)}</location>
            </files>
            <id index="0"/>
          </core>
          <extension encoding="UTF-8" fieldsTerminatedBy="\\t" linesTerminatedBy="\\n" fieldsEnclosedBy="" ignoreHeaderLines="1" rowType="http://rs.tdwg.org/dwc/terms/Occurrence">
            <files>
              <location>{os.path.basename(self.f_occurrences)}</location>
            </files>
            <coreid index="0" />
          </extension>
          <extension encoding="UTF-8" fieldsTerminatedBy="\\t" linesTerminatedBy="\\n" fieldsEnclosedBy="" ignoreHeaderLines="1" rowType="http://rs.iobis.org/obis/terms/ExtendedMeasurementOrFact">
            <files>
              <location>{os.path.basename(self.f_emofs)}</location>
            </files>
            <coreid index="0" />
          </extension>
        </archive>
        """
        tree = etree.ElementTree(etree.fromstring(meta_xml))
        self.add_column_meta_xml(tree, self.events, "core","http://rs.tdwg.org/dwc/terms/Event")
        self.add_column_meta_xml(tree, self.occurrences, "extension", "http://rs.tdwg.org/dwc/terms/Occurrence")
        self.add_column_meta_xml(tree, self.emofs, "extension", "http://rs.iobis.org/obis/terms/ExtendedMeasurementOrFact")
        meta_xml_file = os.path.join(folder, filename)
        self.info("Creating meta.xml file...")
        with open(meta_xml_file, "w") as f:
            f.write(serialize_xml(tree))
        return str(meta_xml_file)


    def add_column_meta_xml(self, tree: etree.ElementTree, df: pd.DataFrame, element: str, row_type: str) -> etree.ElementTree:
        """Adds the index of each dataframe column within the selected element. The element is selected with "element"
        and "row_type"""
        core_event = get_element(tree, f"dwc:{element}", attr="rowType", attr_value=row_type)
        for i, column_name in enumerate(df.columns):
            if column_name == "id":
                continue
            element = etree.SubElement(core_event, "field")
            element.attrib["index"] = str(i)
            element.attrib["term"] = self.column_name_to_term(str(column_name))

    def column_name_to_term(self, colname):
        if colname in self.ris_iobis_terms:
            return self.ris_iobis_prefix + colname
        else:
            return self.dwc_prefix + colname


    def create_eml(self, filename, folder):

        dataset_id = self.dataset["#id"]

        self.info(f"Creating EML file for {dataset_id}")

        template = f"""
            <eml:eml xmlns:eml="https://eml.ecoinformatics.org/eml-2.2.0"
                     xmlns:dc="http://purl.org/dc/terms/"
                     xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
                     xsi:schemaLocation="https://eml.ecoinformatics.org/eml-2.2.0 https://rs.gbif.org/schema/eml-gbif-profile/1.3/eml.xsd"
                     packageId="{dataset_id}" system="http://gbif.org" scope="system"
                     xml:lang="en">
                <dataset/>                
            </eml:eml>
            """

        tree = etree.ElementTree(etree.fromstring(template))
        dataset = get_element(tree, "dataset")
        conf = self.dataset

        # Append alternateIdentifier
        # <alternateIdentifier>3470d506-e667-4e3f-b178-819669684c05</alternateIdentifier>
        create_element(dataset, "title", text=conf["title"])

        people = {p["@people"]: p["role"] for p in conf["contacts"] if "@people" in p.keys()}
        organizations = [o["@organizations"] for o in conf["contacts"] if "@organizations" in o.keys()]


        for p in people.keys():
            self.__add_creator(tree, p, "creator")

        for o in organizations:
            self.__add_metadata_provider(tree, o)

        now = datetime.datetime.now().strftime("%Y-%m-%d")
        create_element(dataset, "datasetName", text=conf["title"])
        create_element(dataset, "pubDate", text=now)
        create_element(dataset, "language", text="en")
        # Add abstract
        abstract = create_element(dataset, "abstract")
        create_element(abstract, "para", text=conf["summary"])

        # contact
        for p, role in people.items():
            if role == "PrincipalInvestigator":
                self.__add_creator(tree, p, "contact")
                break


        # add CC-BY-4.0 license
        text = """This work is licensed under a <ulink url="http://creativecommons.org/licenses/by/4.0/legalcode"><citetitle>Creative Commons Attribution (CC-BY) 4.0 License</citetitle></ulink>."""
        ip = create_element(dataset, "intellectualRights")
        create_element(ip, "para", text=text)

        coverage= create_element(dataset, "coverage")
        geographic_coverage = create_element(coverage, "geographicCoverage")
        description = create_element(geographic_coverage, "geographicDescription", text=f"Station {self.station_name} position")
        bbox = create_element(geographic_coverage, "boundingCoordinates")
        create_element(bbox, "westBoundingCoordinate", text=str(self.longitude))
        create_element(bbox, "eastBoundingCoordinate", text=str(self.longitude))
        create_element(bbox, "northBoundingCoordinate", text=str(self.latitude))
        create_element(bbox, "southBoundingCoordinate", text=str(self.latitude))
        for aphia_id in self.taxa_dict.values():
            taxonomic_coverage = create_element(coverage, "taxonomicCoverage")
            urn = f"urn:lsid:marinespecies.org:taxname:{aphia_id}"
            gen_taxonomic_coverage = create_element(taxonomic_coverage, "generalTaxonomicCoverage", text=urn)
            rank, value = self.__get_aphia_id_details(aphia_id)
            t = create_element(taxonomic_coverage, "taxonomicClassification")
            create_element(t, "taxonRankName", text=rank)
            create_element(t, "taxonRankValue", text=value)


        org_full_name = self.mc.get_document("organizations", organizations[0])["fullName"]
        contact = create_element(dataset, "contact")
        create_element(contact,"organizationName", text=org_full_name)

        meta_xml_file = os.path.join(folder, filename)
        self.info("Creating eml.xml file...")
        with open(meta_xml_file, "w") as f:
            txt = serialize_xml(tree)
            txt = txt.replace("&lt;", "<").replace("&gt;", ">")
            f.write(txt)

        return str(meta_xml_file)


    def __add_creator(self, tree, people_id, key):
        """
        Add creator metadata, like:
            <creator>
              <individualName>Enoc</individualName>
              <surName>Martinez</surName>
              <organizationName>Universitat Politècnica de Catalunya</organizationName>
              <electronicMailAddress>enoc.martinez@upc.edu</electronicMailAddress>
              <userId directory="https://orcid.org/">0000-0003-1233-7105</userId>
            </creator>
        """
        person = self.mc.get_document("people", people_id)
        dataset = get_element(tree, "dataset")
        creator = create_element(dataset, key)
        individual_name = create_element(creator, "individualName")
        create_element(individual_name, "givenName", text=person["givenName"])
        create_element(individual_name, "surName", text=person["familyName"])
        affiliation_id = self.mc.get_affiliation(person)
        affiliation = self.mc.get_document("organizations", affiliation_id)

        create_element(creator, "organizationName", text=affiliation["fullName"])
        create_element(creator, "electronicMailAddress", text=person["email"])
        if "orcid" in person.keys() and person["orcid"]:
            create_element(creator, "userId", attr="directory", attr_value="https://orcid.org/", text=person["orcid"])

    def __add_metadata_provider(self, tree, organization_id):
        """
        <metadataProvider>
            <organizationName>Flanders Marine Institute (VLIZ)</organizationName>
            <address>
                <country>BE</country>
            </address>
            <electronicMailAddress>info@vliz.be</electronicMailAddress>
            <onlineUrl>https://www.vliz.be</onlineUrl>
        </metadataProvider>

        :param tree:
        :param organization_id:
        :return:
        """
        organization = self.mc.get_document("organizations", organization_id)
        dataset = get_element(tree, "dataset")
        provider = create_element(dataset, "metadataProvider")
        create_element(provider, "organizationName", organization["fullName"])
        create_element(provider, "onlineUrl", organization["ROR"])


    def __get_aphia_id_details(self, aphia_id) -> (str, str):
        """
        Gets the rank and value, e.g. returns ("infraorder", Brachyura")
        :param aphia_id:
        :return:
        """

        df = self.__species_cache
        if aphia_id in self.__species_cache["aphiaID"].to_list():
            rank = df[df["aphiaID"] == aphia_id]["taxonRankName"].values[0]
            name = df[df["aphiaID"] == aphia_id]["taxonRankValue"].values[0]
            return rank, name

        self.info("Getting Aphia details from marinespecies.org API")

        url = f"https://www.marinespecies.org/rest/AphiaRecordByAphiaID/{aphia_id}"
        r = requests.get(url)
        results = r.json()
        return results["rank"], results["valid_name"]
