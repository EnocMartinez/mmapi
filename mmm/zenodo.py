import logging
from datetime import datetime, date, timezone
from pathlib import Path
import time
from threading import Thread
from typing import Tuple, List, Optional
import markdown
import requests
import pandas as pd
import os
import json
import numpy as np

from emso_metadata_harmonizer.metadata.emso import init_emso_metadata
from mmm.common import LoggerSuperclass, assert_type, assert_types, get_linked_resource_conf, download_file, CYN, \
    human_readable_bytes, get_file_md5, extract_netcdf_metadata

from mmm.fileserver import FileServer, get_file
import rich


def is_exactly_one_year_interval(df: pd.DataFrame) -> bool:
    # 1. Convert columns to datetime (ensuring UTC/timezone alignment)
    from_dates = pd.to_datetime(df['data_from'], utc=True)
    to_dates = pd.to_datetime(df['data_to'], utc=True)

    # 2. Check if adding exactly 1 calendar year to 'data_from' equals 'data_to'
    # pd.DateOffset(years=1) perfectly handles varying days in leap years (e.g., Feb 29)
    return all(f + pd.DateOffset(years=1) == t for f, t in zip(from_dates, to_dates))


class EuroSciVocResolver:
    """
    Resolves plain-text keywords to their EuroSciVoc term id in Zenodo's
    subjects vocabulary, using an exact (case-insensitive) prefLabel match.

    Results are cached to disk (default: .temp/dicts.json) to avoid
    re-querying Zenodo for the same keyword on every run. Cache entries
    expire after `ttl_days` days and are re-fetched afterwards.
    """

    SCHEME = "EuroSciVoc"

    def __init__(self, api_base: str,
                 cache_path: str = ".temp/euroscivoc.json",
                 ttl_days: float = 30,
                 timeout: int = 30):
        self.cache_path = Path(cache_path)
        self.ttl_seconds = ttl_days * 86400
        self.api_base = api_base.rstrip("/")
        self.timeout = timeout
        self.cache = self._load_cache()
        self.log = logging.getLogger()

    # ------------------------------------------------------------------ #
    # Cache handling
    # ------------------------------------------------------------------ #
    def _load_cache(self) -> dict:
        if not self.cache_path.exists():
            return {}
        try:
            with open(self.cache_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError("Cache root is not a dict")
            return data
        except (json.JSONDecodeError, ValueError, OSError) as e:
            self.log.warning(f"Cache at {self.cache_path} is missing/corrupted ({e}); starting fresh.")
            return {}

    def _save_cache(self) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.cache_path.with_suffix(".tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(self.cache, f, indent=2, ensure_ascii=False)
        tmp_path.replace(self.cache_path)  # atomic write, avoids corrupting cache on crash mid-write

    def _cache_get(self, keyword_key: str) -> Optional[dict]:
        entry = self.cache.get(keyword_key)
        if entry is None:
            return None
        if time.time() - entry.get("cached_at", 0) > self.ttl_seconds:
            return None  # expired, treat as a miss
        return entry

    def _cache_set(self, keyword_key: str, subject_id: Optional[str], label: Optional[str]) -> None:
        self.cache[keyword_key] = {
            "id": subject_id,       # None if no match was found (negative caching, still expires via ttl)
            "label": label,
            "cached_at": time.time(),
        }
        self._save_cache()

    # ------------------------------------------------------------------ #
    # Resolution
    # ------------------------------------------------------------------ #
    def resolve(self, keyword: str) -> Optional[dict]:
        """
        Resolve a single plain-text keyword to a EuroSciVoc subject entry.

        Returns:
            {"id": "<term id>", "label": "<prefLabel>"} on an exact match,
            or None if no exact EuroSciVoc prefLabel match was found
            (a warning is logged in that case).
        """
        key = keyword.strip().lower()

        cached = self._cache_get(key)
        if cached is not None:
            return {"id": cached["id"], "label": cached["label"]} if cached["id"] else None

        subject_id, label = self._query_zenodo(keyword)
        self._cache_set(key, subject_id, label)

        if subject_id is None:
            self.log.warning(f"No exact EuroSciVoc match for keyword '{keyword}'; skipping.")
            return None
        return {"id": subject_id, "label": label}

    def _query_zenodo(self, keyword: str):
        resp = requests.get(
            f"{self.api_base}/subjects",
            params={"suggest": keyword},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        hits = resp.json().get("hits", {}).get("hits", [])

        for hit in hits:
            if hit.get("scheme") != self.SCHEME:
                continue
            label = hit.get("subject", "")
            if label.strip().lower() == keyword.strip().lower():
                return hit["id"], label

        return None, None


class ZenodoResource:
    def __init__(self, row: pd.Series, temp_folder="temp"):
        """
        Input dataframe should have the same columns
        :param row:
        """
        self.log = logging.getLogger()
        assert_type(row, pd.Series)

        self.dataset_id = row["dataset_id"]
        self.resource_id = row["resource_id"]
        self.service = row["service"]
        self.format = row["format"]
        self.data_from = row["data_from"]
        self.data_to = row["data_to"]
        self.path = row["path"]
        self.host = row["host"]
        self.url = row["url"]
        self.creation_date = row["creation_date"]
        self.modification_date = row["modification_date"]
        self.time_min = row["time_min"]
        self.time_max = row["time_max"]
        self.lat_min = row["lat_min"]
        self.lat_max = row["lat_max"]
        self.lon_min = row["lon_min"]
        self.lon_max = row["lon_max"]
        self.depth_min = row["depth_min"]
        self.depth_max = row["depth_max"]
        self.time_min = row["time_min"]
        self.md5 = row["md5"]

        self.doi = ""
        self.zenodo_record = ""
        if row["doi"]:
            self.doi = row["doi"]
        if row["zenodo_record"]:
            self.zenodo_record = row["zenodo_record"]

        self.local_file = ""
        self.temp_folder = temp_folder

        self.basename = os.path.basename(self.path)
        self.year = int(self.data_from.strftime("%Y")) # Year covering the dataset


    def download(self):
        self.local_file = os.path.join(self.temp_folder, self.basename)

        if os.path.exists(self.local_file) and self.md5 == get_file_md5(self.local_file):
            self.log.info(f"Using cached file {self.local_file} (md5 match)")

        elif self.url:
            download_file(self.url, self.local_file)

        else:
            get_file(self.host, self.path, self.local_file)

    def get_file_size(self):
        if not self.local_file or not os.path.exists(self.local_file):
            raise LookupError("Local file does not exist")
        return os.stat(self.local_file).st_size

    def __repr__(self):
        return f"ZenodoResource -> {self.dataset_id}:{self.resource_id}:{self.service}:{self.format}:{self.year}"




class ZenodoClient(LoggerSuperclass):
    def __init__(self, dc: "DataCollector", secrets: dict, fileserver: FileServer, log):
        """
        Zenodo client

        Uses:
        - MetadataCollector for dataset / people / organization metadata
        - FileServer as source when local files are missing
        - Zenodo RDM API for create / update / version / upload / publish
        """
        LoggerSuperclass.__init__(self, log, "ZENODO", colour=CYN)
        assert_type(fileserver, FileServer)

        self.dc = dc
        self.mc = dc.mc
        self.secrets = secrets
        self.fileserver = fileserver
        self.token = secrets["zenodo"]["token"]
        self.url = secrets["zenodo"]["url"]

        self.production = True  # by default we use produciont env!

        self.entries = None  # Here we will store the dataframe from the dataset_registry with all files to be sent
        self.euroscivoc = EuroSciVocResolver(self.url)

    def process_mmapi_dataset(self,
                              dataset_conf: dict,
                              resources: list = None,
                              publish=False,
                              tstart: pd.Timestamp|str="",
                              tend: pd.Timestamp|str="") -> list:
        """
        Entry point called from DataCollector.generate_dataset().

        CLI flags control environment and publish behavior.
        Resource config controls metadata such as title, description,
        access_right, license and resource_type.
        """
        assert_type(dataset_conf, dict)
        assert_types(resources, [list, type(None)])
        assert_types(tstart, [pd.Timestamp, str])
        assert_types(tend, [pd.Timestamp, str])
        if tstart: tstart = pd.Timestamp(tstart)
        if tend: tend = pd.Timestamp(tend)

        try:
            zenodo_resources = dataset_conf["export"]["zenodo"]["resources"]
        except KeyError:
            self.error("export/zenodo/resources not found in dataset config!", exception=KeyError)


        if dataset_conf["export"]["zenodo"].get("yearlyRecord", False):
            self.info("Creating one record for every year!")

        results = []
        for resource in zenodo_resources:
            self.info(f"Processing Zenodo resource {dataset_conf['#id']}")
            result = self.process_zenodo_resource(dataset_conf, resource, publish=publish, tstart=tstart, tend=tend)
            results.append(result)

        return results

    def resolve_zenodo_resource(self, dataset_conf: dict, resource: dict,  tstart: pd.Timestamp | None = None,
                                tend: pd.Timestamp | None = None, yearly=False) -> list:
        """
        Resolve the files that belong to one Zenodo resource.

        Strategy:
        1. Match zenodo.resource.id against fileserver.resource.id
        2. Try dataset_registry first
        3. Fallback to fileserver path scanning

        returns files (list), doi (str), zenodo_record (str), linked_resource, source_service
        """
        assert_type(dataset_conf, dict)
        assert_type(resource, dict)
        assert bool(tstart) == bool(tend), f"start and end time must be both set!"

        dataset_id = dataset_conf["#id"]
        linked_resource, source_service = get_linked_resource_conf(dataset_conf, resource["link"])
        resource_id = linked_resource["id"]
        fmt = linked_resource["format"]

        fs_resources = dataset_conf.get("export", {}).get("fileserver", {}).get("resources", [])
        if not fs_resources:
            raise ValueError(f"Dataset '{dataset_id}' does not define export/fileserver/resources")

        matched = [r for r in fs_resources if r.get("id") == resource_id]
        if not matched:
            raise ValueError(
                f"No fileserver resource found for Zenodo resource '{resource_id}' in dataset '{dataset_id}'"
            )

        query = f"""
            select *
            from {self.mc.dataset_registry_table}
            where
                LOWER(dataset_id) = LOWER('{dataset_id}')
                and LOWER(resource_id) = LOWER('{resource_id}')                  
                and LOWER(service) = LOWER('{source_service}')   
                and LOWER(format) = LOWER('{fmt}');
        """
        if tstart and tend:
            ts = tstart.strftime("%Y-%m-%dT%H:%M:%S")
            te = tend.strftime("%Y-%m-%dT%H:%M:%S")
            query = query.replace(";", f"and data_from >= '{ts}' and data_to <= '{te}';")

        df = self.mc.db.dataframe_from_query(query)
        zrs = [ZenodoResource(row) for _, row in df.iterrows()]

        if yearly:
            # Make sure that all files are of one year, otherwise everything will break
            if not is_exactly_one_year_interval(df):
                self.error(df)
                self.error("The provided datasets are not organized in a yearly manner!", exception=ValueError)

        # download all datasets as threads
        threads = []
        for zr in zrs:
            t = Thread(target=zr.download)
            t.start()
            threads.append(t)

        [t.join() for t in threads]  # wait all tasks to finish
        resources_by_year = {}
        if yearly:
            self.info(f"Creating yearly zenodo records")
            for zr in zrs:
                if zr.year not in resources_by_year.keys():
                    resources_by_year[zr.year] = []
                resources_by_year[zr.year].append(zr)
            return list(resources_by_year.values())

        else:
            return [zrs]


    def process_zenodo_resource(self,dataset_conf: dict,resource: dict,publish=False,
                                tstart: pd.Timestamp | None = None,tend: pd.Timestamp | None = None) -> dict:
        """
        Logic:
        - if CLI says sandbox/prod, use that environment
        - if state exists in that environment:
            - published or DOI exists -> create new version
            - otherwise -> update draft
        - if state does not exist -> create new draft
        - publish only if CLI flag says so
        """
        dataset_id = dataset_conf["#id"]
        api_base = self.url
        access_right = resource.get("access_right", "open")
        license_id = resource.get("license", "cc-by-4.0")
        resource_type = resource.get("resource_type", "dataset")
        token = self.token

        yearly_record = dataset_conf["export"]["zenodo"].get("yearlyRecord", False)

        zenodo_resources = self.resolve_zenodo_resource(dataset_conf, resource, tstart=tstart, tend=tend, yearly=yearly_record)

        # files, doi, zenodo_record, linked_resource, source_service = \
        for resources in zenodo_resources:
            for resource in resources:
                self.info(resource)

            doi = resources[0].doi
            zenodo_record = resources[0].zenodo_record

            assert_type(doi, str)
            assert_type(zenodo_record, str)

            self.info(f"Datsaet_id: {dataset_id}, DOI:{doi}, zenodo_record:{zenodo_record}")
            self.debug(f"{dataset_id} files:")
            for i, zr in enumerate(resources):
                self.debug(f"    {i+1}/{len(resources)} - {zr.basename}")

            title = dataset_conf["export"]["zenodo"].get("title", "")
            if not title:
                title = dataset_conf.get("title") or resource.get("title") or dataset_id

            if "@year@" in title:
                # Assuming yearly dataset
                title = title.replace("@year@", str(resources[0].year))

            payload_create = {
                "access": self.map_access_right(access_right),
                "files": {"enabled": True},
                "metadata": {
                    "title": title,
                    "description": self.build_readme(dataset_conf, resources),
                    "publication_date": datetime.now(timezone.utc).date().isoformat(),
                    "publisher": "Zenodo",
                    "resource_type": {"id": resource_type},
                    "creators": self.build_creators(dataset_conf),
                    "license": {"id": license_id},
                    # "related_identifiers": related_identifiers,
                    "funding": self.build_grants(dataset_conf),
                    "subjects": self.build_keywords(dataset_conf)
                },
            }

            payload_update = {
                "access": payload_create["access"],
                "metadata": payload_create["metadata"],
            }

            uploaded_files = {}
            if not zenodo_record:
                self.info("No previous record detected. Creating new draft.")
                draft = self.rdm_create_draft_record(api_base, token, payload_create)
                record_id = draft["id"]

            else:
                if doi:
                    self.info(f"Published record detected ({zenodo_record}) with DOI {doi}. Creating new version.")
                    current_version = self.rdm_get_current_version(api_base, token, zenodo_record)
                    uploaded_files = self.get_uploaded_files(current_version)
                    draft = self.rdm_create_new_version_draft(api_base, token, int(zenodo_record))
                    record_id = draft["id"]
                    draft = self.rdm_update_draft_record(api_base, token, record_id, payload_update)
                    self.rdm_import_previous_version_files(api_base, token, record_id)
                else:
                    self.info(f"Draft record detected ({zenodo_record}). Updating draft.")
                    draft = self.rdm_update_draft_record(api_base, token, int(zenodo_record), payload_update)
                    record_id = draft["id"]


            self.info(f"Record ready for {dataset_id} -> {record_id}")

            resources_to_upload = []
            for zr in resources:
                if zr.basename in uploaded_files.keys() and zr.md5 == uploaded_files[zr.basename]:
                    self.info(f"Skipping {zr.basename}, md5 hash matches!")
                    continue
                resources_to_upload.append(zr)

            # Delete from draft old versions of the files
            draft_files = self.rdm_list_draft_files(api_base, token, record_id)
            draft_file_keys = {f["key"] for f in draft_files}
            for zr in resources_to_upload:
                if zr.basename in draft_file_keys:
                    self.info(f"Removing existing draft copy of {zr.basename} before re-upload")
                    self.rdm_delete_draft_file(api_base, token, record_id, zr.basename)

            self.rdm_start_file_uploads(api_base, token, record_id, resources_to_upload)

            t = time.time()
            uploaded = 0
            for zr in resources_to_upload:
                f = Path(zr.local_file)
                self.info(f"Uploading {zr.basename} ({human_readable_bytes(f.stat().st_size)})")
                self.rdm_upload_file_content(api_base, token, record_id, zr)
                uploaded += 1

            total_size = sum([zr.get_file_size() for zr in resources_to_upload])
            self.info(f"Uploading {uploaded} files with a total size of {human_readable_bytes(total_size)} took {time.time() - t:.2f} seconds.")

            if publish:
                pub = self.rdm_publish_record(api_base, token, record_id)
                zenodo_record = str(draft["id"])
                self.store_zenodo_record(zenodo_record, resources)
                doi = pub["doi"]
                self.info(f"PUBLISHED {dataset_id} DOI: {doi}")
                self.store_doi(doi, resources)
                self.submit_to_communities(api_base, token, zenodo_record, dataset_conf)

            else:
                self.info(f"Draft kept unpublished for {dataset_id}")
                zenodo_record = str(draft["id"])
                self.store_zenodo_record(zenodo_record, resources)



    def get_uploaded_files(self, current: dict):
        files = {}
        for f in current["files"]:
            md5 = f["checksum"].split(":")[1]
            files[f["key"]] = md5
        return files

    def store_zenodo_record(self, zenodo_record: str, resources: List[ZenodoResource]):
        """
        Stores zenodo record to dataset_registry
        :param zenodo_record:
        :return:
        """
        assert_type(zenodo_record, str)
        assert_type(resources, list)
        [assert_type(zr, ZenodoResource) for zr in resources]
        zr = resources[0]
        dataset_id = zr.dataset_id
        resource_id = zr.resource_id
        service = zr.service
        data_from = [z.data_from for z in resources]
        data_to = [z.data_to for z in resources]

        self.mc.update_zenodo_record(dataset_id, resource_id, service, data_from, data_to, zenodo_record)
        for zr in resources:
            self.info(f"Setting Record to {zenodo_record}, resource {zr}")
            zr.zenodo_record = str(zr.zenodo_record)


    def store_doi(self, doi: str, resources: List[ZenodoResource]):
        """
        Stores zenodo DOI to dataset_registry
        :param zenodo_record:
        :return:
        """
        assert_type(doi, str)
        assert_type(resources, list)
        [assert_type(zr, ZenodoResource) for zr in resources]
        zr = resources[0]
        dataset_id = zr.dataset_id
        resource_id = zr.resource_id
        service = zr.service
        data_from = [z.data_from for z in resources]
        data_to = [z.data_to for z in resources]

        self.mc.update_doi(dataset_id, resource_id, service, data_from, data_to, doi)
        for zr in resources:
            self.info(f"Setting DOI to {doi}, resource {zr}")
            zr.zenodo_record = str(zr.zenodo_record)

    def build_grants(self, dataset_conf: dict):
        # Add Zenodo communities
        grants = []
        assert_type(dataset_conf, dict)

        projects = dataset_conf["funding"].get("@projects", [])
        for project_id in projects:
            self.debug(f"Checking if {project_id} is registered in Zenodo...")
            proj = self.mc.get_document("projects", project_id)
            organization_id = proj["funding"]["@organizations"]

            org = self.mc.get_document("organizations", organization_id)
            org_ror = org.get("ROR", "")
            if org_ror.startswith("https"):
                org_ror = org_ror.split("/")[-1]

            if not org_ror:
                self.warning(f"Could not extract ROR for {organization_id}, ignoring project {project_id}")
                continue

            grant_id = proj["funding"].get("grantId", "")
            if not grant_id:
                continue

            if not self.is_project_registered_in_zenodo(grant_id, org_ror):
                self.warning(f"Project {project_id} not registered in Zenodo")
            else:
                grants.append(
                    {
                        "funder": {"id": org_ror},  # ROR id for European Commission
                        "award": {"id": f"{org_ror}::{grant_id}"}  # funder-id :: award number
                    }
                )

        return grants


    def build_creators(self, dataset_conf: dict) -> list[dict]:
        creators: list[dict] = []
        seen_people: set[str] = set()

        ds_start, ds_end = self.get_dataset_date_window(dataset_conf)

        contacts = dataset_conf.get("contacts", [])
        people_ids = [c.get("@people") for c in contacts if c.get("@people")]

        people_map = {}
        for pid in people_ids:
            person = self.mc.get_document("people", pid)
            if person:
                people_map[pid] = person

        org_ids = self.collect_org_ids_from_people(people_map)

        org_info_map = {}
        for org_id in org_ids:
            org_doc = self.mc.get_document("organizations", org_id)
            if not org_doc:
                continue
            org_info_map[org_id] = {
                "name": self.extract_org_name(org_doc) or org_id,
                "ror": self.extract_ror_from_org_doc(org_doc),
            }

        for c in contacts:
            pid = c.get("@people")
            if not pid or pid in seen_people:
                continue
            seen_people.add(pid)

            person = people_map.get(pid)
            if not person:
                continue

            family = (person.get("familyName") or "").strip()
            given = (person.get("givenName") or "").strip()

            full_name = f"{family}, {given}".strip(", ").strip()
            if not full_name:
                full_name = (person.get("name") or pid).strip()

            person_or_org = {
                "type": "personal",
                "family_name": family or full_name,
                "given_name": given or "",
                "name": full_name,
            }

            orcid = (person.get("orcid") or "").strip()
            if orcid:
                person_or_org["identifiers"] = [{"scheme": "orcid", "identifier": orcid}]

            entry: dict = {"person_or_org": person_or_org}

            chosen = self.pick_affiliation_for_dataset(person.get("affiliations"), ds_start, ds_end)
            org_id = (chosen.get("@organizations") or "").strip() if isinstance(chosen, dict) else ""

            if org_id and org_id in org_info_map:
                org_name = (org_info_map[org_id].get("name") or org_id).strip()
                org_ror = (org_info_map[org_id].get("ror") or "").strip()
                slug = self.ror_slug(org_ror)

                if org_name:
                    if slug:
                        entry["affiliations"] = [{"id": slug, "name": org_name}]
                    else:
                        entry["affiliations"] = [{"name": org_name}]

            creators.append(entry)

        return creators

    def build_keywords(self, dataset_conf: dict) -> list[dict]:
        zenodo_keywords = []
        keywords_text = dataset_conf.get("keywords", [])

        emso = init_emso_metadata()

        # Build keywords based on EMSO Metadata Objects
        keywords = [emso.keywords.keyword_from_label(key_txt) for key_txt in keywords_text]


        for keyword in keywords:
            if keyword.vocab_name.lower() in "gemet":
                keyword_id = keyword.vocab_name.lower() + ":concept/" + keyword.uri.split("/")[-1]
                zenodo_keywords.append({"id": keyword_id})
            elif keyword.vocab_name.lower() in "euroscivoc":
                term = self.euroscivoc.resolve(keyword.name)
                if not term:
                    self.info(f"Could not resolve {keyword.name} to EuroSciVoc")
                    zenodo_keywords.append({"subject": keyword.name})
                else:
                    zenodo_keywords.append(term)
            else:
                zenodo_keywords.append({"subject": keyword.name})
        return zenodo_keywords

    def extract_ror_from_org_doc(self, org_doc: dict) -> str | None:
        ror = org_doc.get("ROR")
        if isinstance(ror, str) and ror.strip():
            return ror.strip()
        return None

    def extract_org_name(self, org_doc: dict) -> str | None:
        for k in ("fullName", "acronym", "name", "label", "title"):
            v = org_doc.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
        return None

    def collect_org_ids_from_people(self, people_map: dict[str, dict]) -> list[str]:
        org_ids: list[str] = []
        for person in people_map.values():
            aff = person.get("affiliations")
            if isinstance(aff, list):
                for a in aff:
                    if isinstance(a, dict):
                        oid = a.get("@organizations")
                        if isinstance(oid, str) and oid.strip():
                            org_ids.append(oid.strip())
        return sorted(set(org_ids))

    def _parse_ymd(self, s: str) -> date | None:
        if not s or not isinstance(s, str):
            return None
        try:
            return datetime.strptime(s.strip(), "%Y-%m-%d").date()
        except ValueError:
            return None

    def _parse_iso_dt_to_date(self, s: str) -> date | None:
        if not s or not isinstance(s, str):
            return None
        try:
            ss = s.strip()
            if ss.endswith("Z") or ss.endswith("z"):
                ss = ss[:-1] + "+00:00"
            return datetime.fromisoformat(ss).date()
        except ValueError:
            return None

    def get_dataset_date_window(self, dataset_doc: dict) -> tuple[date | None, date | None]:
        tr = (dataset_doc.get("constraints") or {}).get("timeRange")

        if isinstance(tr, dict):
            start = self._parse_iso_dt_to_date(tr.get("start"))
            end = self._parse_iso_dt_to_date(tr.get("end"))
            return start, end

        if isinstance(tr, str) and tr.strip():
            parts = [p.strip() for p in tr.split("/", 1)]
            if len(parts) == 2:
                return self._parse_iso_dt_to_date(parts[0]), self._parse_iso_dt_to_date(parts[1])
            one = self._parse_iso_dt_to_date(tr.strip())
            return one, None

        return None, None

    def pick_affiliation_for_dataset(self, affiliations, ds_start: date | None, ds_end: date | None) -> dict | None:
        if not isinstance(affiliations, list) or not affiliations:
            return None

        ds0 = ds_start or ds_end
        ds1 = ds_end or ds_start

        if ds0 is None and ds1 is None:
            for a in affiliations:
                if isinstance(a, dict):
                    return a
            return None

        best = None
        best_score = None

        for a in affiliations:
            if not isinstance(a, dict):
                continue

            a0 = self._parse_ymd(a.get("start"))
            a1 = self._parse_ymd(a.get("end"))

            left0 = a0 or date.min
            left1 = a1 or date.max

            if ds0 is None:
                score = 0
            else:
                if ds1 is not None:
                    overlap = not (left1 < ds0 or left0 > ds1)
                else:
                    overlap = (left0 <= ds0 <= left1)

                if overlap:
                    score = 0
                else:
                    if left1 < ds0:
                        score = (ds0 - left1).days
                    else:
                        score = (left0 - ds0).days

            if best_score is None or score < best_score:
                best_score = score
                best = a

        return best


    def build_related_identifiers_from_urls(self, urls: list[str]) -> list[dict]:
        out = []
        seen = set()
        for u in urls:
            if not u or u in seen:
                continue
            seen.add(u)
            out.append({
                "identifier": u,
                "scheme": "url",
                "relation_type": {"id": "references"},
            })
        return out

    def ror_slug(self, ror: str) -> str | None:
        if not isinstance(ror, str):
            return None
        r = ror.strip()
        if not r:
            return None
        if "ror.org/" in r:
            return r.split("ror.org/", 1)[1].strip().strip("/")
        return r.strip().strip("/")

    def path_to_fileserver_url(self, local_path: str) -> str:
        p = local_path.replace("\\", "/")
        if p.startswith("/opt/files/"):
            return self.fileserver_base_url.rstrip("/") + "/" + p[len("/opt/files/"):]
        if p.startswith("http://") or p.startswith("https://"):
            return p
        raise ValueError(f"Cannot map local path to fileserver URL: {local_path}")

        self.info(f"Downloaded file to temporary cache: {tmp_path}")
        return tmp_path

    def zenodo_headers(self, token: str, json_headers: bool = False) -> dict:
        headers = {"Authorization": f"Bearer {token}"}
        if json_headers:
            headers["Content-Type"] = "application/json"
        return headers

    def rdm_get_current_version(self,api_base: str, token: str, record_id):
        url = f"{api_base}/records/{record_id}"
        r = requests.get(url, headers=self.zenodo_headers(token, True), timeout=60)
        self.http_response(r)
        return r.json()

    def rdm_create_draft_record(self, api_base: str, token: str, payload: dict) -> dict:
        url = f"{api_base}/records"
        r = requests.post(url, json=payload, headers=self.zenodo_headers(token, True), timeout=60)
        self.http_response(r)
        return r.json()

    def rdm_update_draft_record(self, api_base: str, token: str, record_id: str | int, payload: dict) -> dict:
        url = f"{api_base}/records/{record_id}/draft"
        r = requests.put(url, json=payload, headers=self.zenodo_headers(token, True), timeout=60)
        self.http_response(r)
        return r.json()

    def rdm_create_new_version_draft(self, api_base: str, token: str, record_id: str | int) -> dict:
        url = f"{api_base}/records/{record_id}/versions"
        r = requests.post(url, headers=self.zenodo_headers(token), timeout=60)
        self.http_response(r)
        return r.json()

    def rdm_list_draft_files(self, api_base: str, token: str, record_id: str | int) -> list[dict]:
        url = f"{api_base}/records/{record_id}/draft/files"
        r = requests.get(url, headers=self.zenodo_headers(token), timeout=60)
        self.http_response(r)
        data = r.json()
        return data.get("entries", [])

    def rdm_start_file_uploads(self, api_base: str, token: str, record_id: str | int, zresources: List[ZenodoResource]):
        url = f"{api_base}/records/{record_id}/draft/files"
        files = [zr.basename for zr in zresources]
        body = [{"key": fn} for fn in files]
        r = requests.post(url, json=body, headers=self.zenodo_headers(token, True), timeout=60)
        self.http_response(r)

    def rdm_upload_file_content(self, api_base: str, token: str, record_id: str | int, zresource: ZenodoResource) -> None:
        url = f"{api_base}/records/{record_id}/draft/files/{zresource.basename}/content"

        with open(zresource.local_file, "rb") as f:
            r = requests.put(
                url,
                data=f,
                headers={**self.zenodo_headers(token, json_headers=False)},
                timeout=300,
            )

        self.http_response(r)
        self.rdm_commit_file(api_base, token, record_id, zresource.basename)

    def http_response(self, r):
        try:
            r.raise_for_status()
        except Exception as e:
            self.error(e)
            self.error(f"Response: {r.text}")
            raise e

    def rdm_commit_file(self, api_base: str, token: str, record_id: str | int, filename: str) -> None:
        url = f"{api_base}/records/{record_id}/draft/files/{filename}/commit"

        r = requests.post(url, headers=self.zenodo_headers(token), timeout=60)
        self.http_response(r)

    def rdm_publish_record(self, api_base: str, token: str, record_id: str | int) -> dict:
        url = f"{api_base}/records/{record_id}/draft/actions/publish"
        r = requests.post(url, headers=self.zenodo_headers(token), timeout=60)
        self.http_response(r)
        return r.json()

    def rdm_import_previous_version_files(self, api_base: str, token: str, record_id: str | int) -> dict:
        url = f"{api_base}/records/{record_id}/draft/actions/files-import"
        r = requests.post(url, headers=self.zenodo_headers(token), timeout=60)
        self.http_response(r)
        return r.json()

    def rdm_delete_draft_file(self, api_base: str, token: str, record_id: str | int, filename: str) -> None:
        url = f"{api_base}/records/{record_id}/draft/files/{filename}"
        r = requests.delete(url, headers=self.zenodo_headers(token), timeout=60)
        self.http_response(r)

    def map_access_right(self, access_right: str) -> dict:
        ar = (access_right or "open").strip().lower()
        if ar == "open":
            return {"record": "public", "files": "public"}
        return {"record": "restricted", "files": "restricted"}

    def submit_to_communities(self, api_base, token, record_id: str, dataset_conf: dict):
        communities = []
        communities_str = ""
        for c in dataset_conf["export"]["zenodo"].get("communities", []):
            communities.append({"id": c})
            communities_str += f"'{c}' "

        self.info(f"Submitting record {record_id} to communities: {communities_str}")
        r = requests.post(
            f"{api_base}/records/{record_id}/communities",
            json={"communities": communities},
            headers=self.zenodo_headers(token)
        )
        self.http_response(r)

    def __md_process_sensors(self, text: str, metadata_list: list, metadata2: dict):
        assert_type(text, str)
        assert_type(metadata_list, list)
        [assert_type(x, dict) for x in metadata_list]
        key = "@sensors@"
        if key not in text: return text

        sensors = []
        if metadata_list:
            # Get info from NetCDF files
            for meta in metadata_list:
                for varname, varmeta in meta["variables"].items():
                    if varmeta.get("variable_type", "") == "sensor":
                        name = varmeta.get("sdn_instrument_name", "")
                        if not name:
                            name = varmeta.get("long_name", "")
                        if not name:
                            raise LookupError(f"Could not get sensor name for '{varname}'")

                        if name and name not in sensors:
                            sensors.append(name)
        else:
            # Get info from MMAPI DB
            for sensor in metadata2["sensors"].values():
                sensors.append(sensor["long_name"])

        sensors = np.unique(sensors).tolist()
        sensors_text =  ", ".join(sensors)
        return text.replace(key, sensors_text)

    def __md_process_platforms(self, text: str, metadata_list: list,  metadata2: dict):
        assert_type(text, str)
        assert_type(metadata_list, list)
        [assert_type(x, dict) for x in metadata_list]
        key = "@platforms@"
        if key not in text: return text

        platforms = []
        for meta in metadata_list:
            for varname, varmeta in meta["variables"].items():
                if varmeta.get("variable_type", "") == "platform":
                    name = varmeta.get("sdn_instrument_name", "")
                    if not name:
                        name = varmeta.get("long_name", "")
                    if name and name not in platforms:
                        platforms.append(name)

        platforms = np.unique(platforms).tolist()

        platforms_text = ", ".join(platforms)
        return text.replace(key, platforms_text)

    def __md_process_coordinates(self, text: str, resources: List[ZenodoResource]):
        assert_type(text, str)
        assert_type(resources, list)
        [assert_type(x, ZenodoResource) for x in resources]
        key = "@coordinates@"
        if key not in text: return text

        lats_min = [z.lat_min for z in resources]
        lats_max = [z.lat_max for z in resources]
        lons_min = [z.lon_min for z in resources]
        lons_max = [z.lon_max for z in resources]
        depths_min = [z.depth_min for z in resources]
        depths_max = [z.depth_max for z in resources]

        lat_min = min(lats_min)
        lat_max = max(lats_max)
        lon_min = min(lons_min)
        lon_max = max(lons_max)
        depth_min = min(depths_min)
        depths_max = max(depths_max)

        coordinates_text = "latitude  "
        if lat_min == lat_max:
            coordinates_text += f"{lat_min} °N"
        else:
            coordinates_text += f"{lat_min} - {lat_max} °N"

        coordinates_text += ", longitude "
        if lon_min == lon_max:
            coordinates_text += f"{lon_min} °E"
        else:
            coordinates_text += f"{lon_min} - {lon_max} °N"

        coordinates_text += ", depth "
        if depth_min == depths_max:
            coordinates_text += f"{depth_min} m"
        else:
            coordinates_text += f"{depth_min} - {depths_max} m"

        return text.replace(key, coordinates_text)

    def __md_process_temporal_coverage(self, text: str, resources: List[ZenodoResource]):
        assert_type(text, str)
        assert_type(resources, list)
        [assert_type(x, ZenodoResource) for x in resources]
        key = "@temporal_coverage@"
        if key not in text: return text

        time_mins = [z.time_min for z in resources]
        time_maxs = [z.time_max for z in resources]

        tmin = min(time_mins).strftime("%Y-%m-%d")
        tmax = max(time_maxs).strftime("%Y-%m-%d")

        time_text = f"from {tmin} to {tmax}"
        return text.replace(key, time_text)

    def __md_process_varibale_table(self, text: str, metadata_list: list, metadata2: dict):
        assert_type(text, str)
        assert_type(metadata_list, list)
        [assert_type(x, dict) for x in metadata_list]
        key = "@variable_table@"

        variables = []  # list of lists (name, description, units)
        processed_vars = []
        for metadata in metadata_list:
            for k, meta in metadata["variables"].items():
                if k in processed_vars:
                    continue

                else:
                    processed_vars.append(k)

                if meta["variable_type"] == "coordinate":
                    continue

                elif meta["variable_type"] == "environmental":
                    variables.append(
                        [k, meta["long_name"], meta["sdn_uom_name"]]
                    )

                elif meta["variable_type"] in ["technical", "biological"]:
                    units = meta.get("units", "n/a")
                    variables.append(
                        [k, meta["long_name"], units]
                    )
        # Now, construct Markdown table

        table = "| variable | description | units |\n"
        table += "|----|----|----|\n"
        for v, d, u in variables:
            table += f"| {v} | {d} | {u} |\n"
        return text.replace(key, table)


    def build_readme(self, dataset_conf: dict, resources: List[ZenodoResource]):
        assert_type(dataset_conf, dict)
        assert_type(resources, list)
        [assert_type(x, ZenodoResource) for x in resources]

        md_text = dataset_conf["export"]["zenodo"]["&readme"]

        files = [z.local_file for z in resources]

        metadata = []
        for f in files:
            if not str(f).endswith(".nc"):
                continue
            t = time.time()
            m = extract_netcdf_metadata(f)
            self.info(f"Opening {f} as a dict ({time.time() - t:.2f} s)")
            metadata.append(m)

        metadata2 = self.dc.metadata_harmonizer_conf(dataset_conf)

        # Metadata can be extracte from 2 different places, directly from NetCDF files (if present) or from the mmapi
        # database. Extract both and let the __md_process__xxx functions decide the best option. For instance, sensor
        # metadata is present in both, but temporal and spatial coverage is only in NetCDF files.

        # metadata is a list of NetCDF file metadata
        # metadata2 is a dict with the mmapi DB metadata

        md_text = self.__md_process_sensors(md_text, metadata, metadata2)
        md_text = self.__md_process_platforms(md_text, metadata, metadata2)
        md_text = self.__md_process_coordinates(md_text, resources)
        md_text = self.__md_process_temporal_coverage(md_text, resources)
        md_text = self.__md_process_varibale_table(md_text, metadata, metadata2)
        html = markdown.markdown(md_text, extensions=['tables'])
        return html

    def is_project_registered_in_zenodo(self, project_id: str, funder_ror: str) -> dict | None:
        """
        Check whether a funded project/award is registered in Zenodo's award vocabulary.

        Zenodo award ids follow the pattern "<funder_id>::<award_number>", and each
        award entry lists its source identifiers (e.g. a CORDIS URL for EU projects).
        This checks the /api/awards suggest endpoint for a hit whose funder matches
        `funder_ror` and whose award number / identifiers match `project_id`.

        Args:
            project_id: the project/award number (e.g. "101008724" for a CORDIS
                project, or a grant number for other funders).
            funder_ror: the ROR id of the funder (e.g. "00k4n6c32" for the
                European Commission). Only the ROR scheme is checked here since
                that's what Zenodo's funding.funder.id expects.

        Returns:
            The matching award dict (as returned by the API) if found, else None.
            On a match, `result["id"]` is the exact string to use as
            `metadata.funding[i].award.id` when creating a Zenodo record.
        """

        resp = requests.get(f"{self.url}/awards", params={"suggest": str(project_id)}, timeout=30)
        resp.raise_for_status()
        hits = resp.json().get("hits", {}).get("hits", [])


        for hit in hits:
            funder_id = hit.get("funder", {}).get("id", "")
            if funder_id != funder_ror:
                continue

            # Match on the award number field...
            if str(hit.get("number", "")) == str(project_id):
                return hit

            # ...or on any identifier containing the project id (covers CORDIS URLs etc.)
            for ident in hit.get("identifiers", []):
                if str(project_id) in ident.get("identifier", ""):
                    return hit

        return None
