import logging
from datetime import datetime, date, timezone
from pathlib import Path
import time
from threading import Thread
from typing import Tuple
import markdown
import requests
import pandas as pd
import os

from emso_metadata_harmonizer.metadata.emso import init_emso_metadata
from mmm.common import LoggerSuperclass, assert_type, assert_types, get_linked_resource_conf, download_file, CYN, \
    human_readable_bytes, get_file_md5, extract_netcdf_metadata
from mmm import MetadataCollector
from mmm.fileserver import FileServer, get_file


class RemoteDataset:
    def __init__(self, url, path, host, temp_folder="temp"):
        self.host = host
        self.url = url
        self.path = path
        self.temp_folder = temp_folder

        self.log = logging.getLogger()

        self.local_file = ""

    def download(self):
        basename = os.path.basename(self.path)
        self.file = os.path.join(self.temp_folder, basename)

        if os.path.exists(self.file):
            self.log.info(f"Using cached file {self.file}")

        elif self.url:
            download_file(self.url, self.file)

        else:
            get_file(self.host, self.path, self.file)


class ZenodoClient(LoggerSuperclass):
    def __init__(self, mc: MetadataCollector, secrets: dict, fileserver: FileServer, log):
        """
        Zenodo client

        Uses:
        - MetadataCollector for dataset / people / organization metadata
        - FileServer as source when local files are missing
        - Zenodo RDM API for create / update / version / upload / publish
        """
        LoggerSuperclass.__init__(self, log, "ZENODO", colour=CYN)
        assert_type(fileserver, FileServer)

        self.mc = mc
        self.secrets = secrets
        self.fileserver = fileserver
        self.token = secrets["zenodo"]["token"]
        self.url = secrets["zenodo"]["url"]

        self.production = True  # by default we use produciont env!
        if "sandbox" in self.url:
            self.production = False

        self.entries = None  # Here we will store the dataframe from the dataset_registry with all files to be sent

    def process_mmapi_dataset(self, dataset_conf: dict, resources: list = None,  publish=False) -> list:
        """
        Entry point called from DataCollector.generate_dataset().

        CLI flags control environment and publish behavior.
        Resource config controls metadata such as title, description,
        access_right, license and resource_type.
        """
        assert_type(dataset_conf, dict)
        assert_types(resources, [list, type(None)])

        try:
            zenodo_resources = dataset_conf["export"]["zenodo"]["resources"]
        except KeyError:
            self.error("export/zenodo/resources not found in dataset config!", exception=KeyError)

        results = []
        for resource in zenodo_resources:
            self.info(f"Processing Zenodo resource {dataset_conf['#id']}:{resource['id']}")
            result = self.process_zenodo_resource(dataset_conf, resource, publish=publish)
            results.append(result)

        return results

    def resolve_zenodo_resource(self, dataset_conf: dict, resource: dict) -> Tuple[list[Path], str, str, str, str]:
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
        dataset_id = dataset_conf["#id"]
        resource_id = resource["id"]
        linked_resource, source_service = get_linked_resource_conf(dataset_conf, resource["link"])

        fs_resources = dataset_conf.get("export", {}).get("fileserver", {}).get("resources", [])
        if not fs_resources:
            raise ValueError(f"Dataset '{dataset_id}' does not define export/fileserver/resources")

        matched = [r for r in fs_resources if r.get("id") == resource_id]
        if not matched:
            raise ValueError(
                f"No fileserver resource found for Zenodo resource '{resource_id}' in dataset '{dataset_id}'"
            )

        query = f"""
            select dataset_id, resource_id, data_from, data_to, url, path, host, doi, zenodo_record
            from {self.mc.dataset_registry_table}
            where
                LOWER(dataset_id) = LOWER('{dataset_id}')
                and LOWER(resource_id) = LOWER('{resource_id}')
                and LOWER(service) = LOWER('{source_service}');
        """
        df = self.mc.db.dataframe_from_query(query)
        self.entries = df

        assert len(df["doi"].unique()) < 2, f"Multiple DOIs registered detected for dataset {dataset_id}!"
        assert len(df["zenodo_record"].unique()) < 2, f"Multiple zenodo_records registered detected for dataset {dataset_id}!"

        doi = str(df["doi"].values[0])
        zenodo_record = str(df["zenodo_record"].values[0])

        if doi == "None": doi = ""
        if zenodo_record == "None": zenodo_record = ""

        self.info(f"Found {len(df)} files to be uploaded")


        # download all datasets as threads
        threads = []
        datasets = []
        for _, row in df.iterrows():
            d = RemoteDataset(row["url"], row["path"], row["host"])
            t = Thread(target=d.download)
            t.start()
            threads.append(t)
            datasets.append(d)

        [t.join() for t in threads]  # wait all tasks to finish

        return sorted([Path(d.file) for d in datasets]), doi, zenodo_record, linked_resource, source_service


    def process_zenodo_resource(self, dataset_conf: dict, resource: dict, publish=False) -> dict:
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

        files, doi, zenodo_record, linked_resource, source_service = self.resolve_zenodo_resource(dataset_conf, resource)

        assert_type(doi, str)
        assert_type(zenodo_record, str)

        self.info(f"Datsaet_id: {dataset_id}, DOI:{doi}, zenodo_record:{zenodo_record}")
        self.debug(f"{dataset_id} files:")
        for i, f in enumerate(files):
            self.debug(f"    {i+1}/{len(files)} - {f.name}")

        title = dataset_conf.get("title") or resource.get("title") or dataset_id

        payload_create = {
            "access": self.map_access_right(access_right),
            "files": {"enabled": True},
            "metadata": {
                "title": title,
                "description": self.build_readme(dataset_conf, files),
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


        self.info(f"Record ready for {dataset_id}:{resource['id']} -> {record_id}")

        filenames = [p.name for p in files]
        self.rdm_start_file_uploads(api_base, token, record_id, filenames)

        files_to_upload = []
        for f in files:
            basename = os.path.basename(f)
            if basename in uploaded_files.keys() and get_file_md5(f) == uploaded_files[basename]:
                self.info(f"Skipping {f}, md5 hash matches!")
                continue
            files_to_upload.append(f)

        t = time.time()
        uploaded = 0
        for f in files_to_upload:
            local_f = self.ensure_local_file(f)
            self.info(f"Uploading {local_f} ({human_readable_bytes(local_f.stat().st_size)})")
            self.rdm_upload_file_content(api_base, token, record_id, f.name, local_f)
            uploaded += 1

        total_size = sum([f.stat().st_size for f in files])
        self.info(f"Uploading {uploaded} files with a total size of {human_readable_bytes(total_size)} took {time.time() - t:.2f} seconds.")

        if publish:
            pub = self.rdm_publish_record(api_base, token, record_id)
            zenodo_record = str(draft["id"])
            self.store_zenodo_record(zenodo_record, source_service)
            doi = pub["doi"]
            self.info(f"PUBLISHED {dataset_id}:{resource['id']} DOI: {doi}")
            self.store_doi(doi, source_service)
            self.submit_to_communities(api_base, token, zenodo_record, dataset_conf)
            ret =  pub
        else:
            self.info(f"Draft kept unpublished for {dataset_id}:{resource['id']}")
            zenodo_record = str(draft["id"])
            self.store_zenodo_record(zenodo_record, source_service)
            ret = draft


        return ret

    def get_uploaded_files(self, current: dict):
        files = {}
        for f in current["files"]:
            md5 = f["checksum"].split(":")[1]
            files[f["key"]] = md5
        return files

    def store_zenodo_record(self, zenodo_record: str, service: str):
        """
        Stores zenodo record to dataset_registry
        :param zenodo_record:
        :return:
        """
        assert_type(zenodo_record, str)
        assert_type(service, str)

        dataset_id = self.entries["dataset_id"].values[0]
        resource_id = self.entries["resource_id"].values[0]
        data_from = self.entries["data_from"].to_list()
        data_to = self.entries["data_to"].to_list()
        self.mc.update_zenodo_record(dataset_id, resource_id, service, data_from, data_to, zenodo_record)

    def store_doi(self, doi: str, service: str):
        """
        Stores zenodo DOI to dataset_registry
        :param zenodo_record:
        :return:
        """
        assert_type(doi, str)
        assert_type(service, str)

        dataset_id = self.entries["dataset_id"].values[0]
        resource_id = self.entries["resource_id"].values[0]
        data_from = self.entries["data_from"].to_list()
        data_to = self.entries["data_to"].to_list()
        self.mc.update_doi(dataset_id, resource_id, service, data_from, data_to, doi)

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

        zenodo_supported_vocabularies = ["gemet", "euroscivoc"]

        for keyword in keywords:
            if keyword.vocab_name.lower() in zenodo_supported_vocabularies:
                # zenodo_keywords.append({"id": keyword.uri})
                # TODO: Process keywords properly!
                keyword_id = keyword.vocab_name.lower() + ":concept/" + keyword.uri.split("/")[-1]
                zenodo_keywords.append({"id": keyword_id})
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

    def ensure_local_file(self, path: Path) -> Path:
        if path.exists():
            self.debug(f"Local file found: {path}")
            return path

        url = self.path_to_fileserver_url(str(path))

        self.info(f"Local file missing, downloading from fileserver: {url}")

        tmp_dir = Path("/tmp/zenodo_upload_cache")
        tmp_dir.mkdir(parents=True, exist_ok=True)
        tmp_path = tmp_dir / path.name

        r = requests.get(url, stream=True, timeout=300)
        self.http_response(r)

        with open(tmp_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    f.write(chunk)

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

    def rdm_start_file_uploads(self, api_base: str, token: str, record_id: str | int, filenames: list[str]) -> set[str]:
        url = f"{api_base}/records/{record_id}/draft/files"

        existing = {
            entry.get("key")
            for entry in self.rdm_list_draft_files(api_base, token, record_id)
            if entry.get("key")
        }

        missing = [fn for fn in filenames if fn not in existing]
        self.debug(f"Missing files {missing}")

        if not missing:
            self.debug("All file entries already exist in draft")
            return set()

        body = [{"key": fn} for fn in missing]
        r = requests.post(url, json=body, headers=self.zenodo_headers(token, True), timeout=60)
        self.http_response(r)
        return set(missing)

    def rdm_upload_file_content(self, api_base: str, token: str, record_id: str | int, filename: str, file_path: Path) -> None:
        url = f"{api_base}/records/{record_id}/draft/files/{filename}/content"

        with file_path.open("rb") as f:
            r = requests.put(
                url,
                data=f,
                headers={**self.zenodo_headers(token, json_headers=False)},
                timeout=300,
            )

        self.http_response(r)
        self.rdm_commit_file(api_base, token, record_id, filename)

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

    def __md_process_sensors(self, text: str, metadata_list: list ):
        assert_type(text, str)
        assert_type(metadata_list, list)
        [assert_type(x, dict) for x in metadata_list]
        key = "$sensors$"
        if key not in text: return text

        sensors = []
        for meta in metadata_list:
            for varname, varmeta in meta["variables"].items():
                if varmeta.get("variable_type", "") == "sensor":
                    name = varmeta.get("long_name", "")
                    if name and name not in sensors:
                        sensors.append(name)

        sensors_text =  ", ".join(sensors)
        return text.replace(key, sensors_text)

    def __md_process_platforms(self, text: str, metadata_list: list):
        assert_type(text, str)
        assert_type(metadata_list, list)
        [assert_type(x, dict) for x in metadata_list]
        key = "$platforms$"
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

        platforms_text = ", ".join(platforms)
        return text.replace(key, platforms_text)

    def __md_process_coordinates(self, text: str, metadata_list: list):
        assert_type(text, str)
        assert_type(metadata_list, list)
        [assert_type(x, dict) for x in metadata_list]
        key = "$coordinates$"
        if key not in text: return text

        lats_min = []
        lats_max = []
        lons_min = []
        lons_max = []
        depths_min = []
        depths_max = []

        for meta in metadata_list:
            lats_min.append(meta["global"]["geospatial_lat_min"])
            lats_max.append(meta["global"]["geospatial_lat_max"])
            lons_min.append(meta["global"]["geospatial_lon_min"])
            lons_max.append(meta["global"]["geospatial_lon_max"])
            depths_min.append(meta["global"]["geospatial_vertical_min"])
            depths_max.append(meta["global"]["geospatial_vertical_max"])

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

    def __md_process_temporal_coverage(self, text: str, metadata_list: list):
        assert_type(text, str)
        assert_type(metadata_list, list)
        [assert_type(x, dict) for x in metadata_list]
        key = "$temporal_coverage$"
        if key not in text: return text

        tmins = []
        tmaxs = []

        for meta in metadata_list:
            tmins.append(pd.Timestamp(meta["global"]["time_coverage_start"]))
            tmaxs.append(pd.Timestamp(meta["global"]["time_coverage_end"]))

        tmin = min(tmins).strftime("%Y-%m-%d")
        tmax = max(tmaxs).strftime("%Y-%m-%d")

        time_text = f"from {tmin} to {tmax}"
        return text.replace(key, time_text)

    def __md_process_varibale_table(self, text: str, metadata_list: list):
        assert_type(text, str)
        assert_type(metadata_list, list)
        [assert_type(x, dict) for x in metadata_list]
        key = "$variable_table$"

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


    def build_readme(self, dataset_conf: dict, files: list):
        """
        Builds a README from files
        :param files:
        :return:
        """

        assert_type(dataset_conf, dict)
        assert_type(files, list)
        [assert_type(x, Path) for x in files]

        md_text = dataset_conf["export"]["zenodo"]["readme"]

        metadata = []

        for f in files:
            if not str(f).endswith(".nc"):
                self.warning(f"File extension not supported for auto-build readme {f.split('.')[-1]}")
                continue
            t = time.time()
            m = extract_netcdf_metadata(f)
            self.info(f"Opening {f} as a dict ({time.time() - t:.2f} s)")
            metadata.append(m)

        md_text = self.__md_process_sensors(md_text, metadata)
        md_text = self.__md_process_platforms(md_text, metadata)
        md_text = self.__md_process_coordinates(md_text, metadata)
        md_text = self.__md_process_temporal_coverage(md_text, metadata)
        md_text = self.__md_process_varibale_table(md_text, metadata)
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
