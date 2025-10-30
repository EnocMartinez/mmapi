from mmm.common import LoggerSuperclass
import requests
import json

class MmapiClient(LoggerSuperclass):
    """
    This class handles HTTP requests to the MMAPI and stores a cached memory for efficiency
    """
    def __init__(self, secrets: dict, log):
        LoggerSuperclass.__init__(self, log, "API")
        self.info("MMAPI API Client")

        self.url = secrets["mmapi"]["root_url"]
        self.cache = {}  # in cache, we will store the whole collection as a dict with key=id value=doc

    def get_docs(self, endpoint) -> dict:
        """
        Get the documents from the API and stores them into cache
        :param endpoint: doc collection
        :return: dict with all the collection
        """
        if endpoint not in self.cache.keys():
            # Get endpoint and order as a dict
            self.cache[endpoint] = {doc["#id"]: doc for doc in self.get_from_api(endpoint)}
        return self.cache[endpoint]

    def get_doc(self, endpoint, doc_id) -> dict:
        docs = self.get_docs(endpoint)
        return docs[doc_id]

    def get_ids(self, endpoint) -> list:
        if endpoint.startswith("@"):
            endpoint = endpoint[1:]
        return list(self.get_docs(endpoint).keys())

    def get_schemas(self) -> list:
        return self.get_from_api("schemas")

    def get_metadata_schemas(self) -> dict:
        return self.get_from_api("metadata_schema")


    def get_from_api(self, endpoint) -> list|dict:
        self.debug(f"Getting {endpoint}")
        url = self.url + "/mmapi/v1.0/" + endpoint
        r = requests.get(url)
        if r.status_code > 300:
            self.error(f"{url} returned {r.status_code}! text {r.text}", exception=ValueError)
        return json.loads(r.text)

    def validate_doc(self, collection, doc) -> (bool, str):
        """
        Validates a document
        :param doc:
        :param collection:
        :return:
        """
        self.debug(f"Validating {doc['#id']} against {collection}")
        url = self.url + "/mmapi/v1.0/validate/" + collection
        r = requests.post(url, data=json.dumps(doc), headers={"Content-Type": "application/json"})
        if r.status_code > 300:
            self.error(f"{url} returned {r.status_code}! text {r.text}", exception=ValueError)
            return False, "HTTP Error"


        resp = json.loads(r.text)
        if resp["success"]:
            return True, ""

        return False, resp["message"]
