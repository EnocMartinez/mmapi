#!/usr/bin/env python3
"""
CKAN API client to publish/update datasets and dataset metadata

author: Enoc Martínez
institution: Universitat Politècnica de Catalunya (UPC)
email: enoc.martinez@upc.edu
license: MIT
created: 23/3/21
"""
import traceback

import requests
import json
from mmm.common import normalize_string, LoggerSuperclass, PRL, assert_type, download_file, run_over_ssh, check_url, \
    file_list, assert_types, RST, BLU, WHT, CYN
from mmm.fileserver import FileServer
from mmm import MetadataCollector
from PIL import Image
import logging
import os
from mmm.plotter import auto_plotter
import rich
import time

def resize_pic(image: Image, max_width=1920):
    original_width, original_height = image.size

    if original_width > max_width:
        scaling_factor = max_width / original_width
        new_width = max_width
        new_height = int(original_height * scaling_factor)
        image = image.resize((new_width, new_height), Image.LANCZOS)
    return image

def process_extras(extras: dict) -> list:
    """
    Converts from {"myparam": "myvalue"} to CKAN extras like [{"key": "myparam", "value": "myvalue"}]
    :param extras: key-value dict. Values should be strings ints or flotas
    :return: list of CKAN extras
    """
    assert(type(extras) == dict)
    processed = []
    for key, value in extras.items():
        if not isinstance(value, str):
            value = str(value)
        processed.append({"key": key, "value": value})
    return processed


def are_they_equal(element1, element2, key="") -> bool:
    """
    Return True if the two elements are equal, otherwise return false
    """
    if type(element1) != type(element2):
        raise ValueError(f"different types found!, {element1} {element2}")

    if type(element1) in [str, float, int, bool]:
        if element1 != element2:
            return False

    elif isinstance(element1, list):

        if key == "extras":
            sortby = "key"
        elif key == "groups":
            sortby = "id"
        else:
            raise ValueError(f"Cannot sort list of elements with key='{key}'")

        element1 = sorted(element1, key=lambda x: x[sortby])
        element2 = sorted(element2, key=lambda x: x[sortby])


        for v1, v2 in zip(element1, element2):
            if not are_they_equal(v1, v2):
                return False

    elif isinstance(element1, dict):
        for key, value in element1.items():
            if not are_they_equal(element1[key], element2[key], key=key):
                return False

    else:
        raise ValueError(f"Unimplemented data type {type(element1)}")

    return True


def updated_required(obj_list, obj) -> bool:
    """
    Checks if the target object needs to be updated. Return FALSE only if ALL parameters are exactly equal
    the obj_list there may be additional params
    :param obj_list: list of dicts coming directly from CKAN
    :param obj: to check if it's in the later version
    :return: True/False
    """

    obj_id = obj["id"]
    existing = None
    for o in obj_list:
        if "id" not in o.keys():
            continue
        if o["id"] == obj_id:
            existing = o
            break

    if not existing:
        raise ValueError(f"Object with id '{obj_id}' not found in list!")

    # if they are equal, no update required! Otherwise yes
    return not are_they_equal(obj, existing)



class CkanClient(LoggerSuperclass):

    def __init__(self, mc: MetadataCollector, url: str, api_key: str, fileserver: FileServer, log: logging.Logger,
                 proj_logos_url="", org_logos_url=""):
        """
        Creates a CKAN client, able to publish datasets
        """

        LoggerSuperclass.__init__(self, log, "CKAN", colour=PRL)
        assert_type(fileserver, FileServer)
        self.mc = mc
        self.url = url
        if self.url[-1] != "/":
            self.url += "/"
        self.url = self.url + "api/3/action/"
        self.api_key = api_key
        self.proj_logos_url = proj_logos_url
        self.org_logos_url = org_logos_url
        self.fileserver = fileserver

        self.__cache_time = 300 # use cached values if they were processed less than 300 seconds ago
        self.__cache = {
            # cached list of elements, used to speed up things
            # key->endpoint, value ->(cached_time, list)
        }

    def upload_data_link(self, dataset_id, name, description, link):
        return self.resource_create(dataset_id, name.lower() + "_csv_data", name=name, description=description,
                                      resource_url=link, format="csv")

    # --------- Packages (Datasets) ---------#
    def get_packages(self) -> list:
        """
        Get packages with their resources
        """
        url = self.url + "current_package_list_with_resources"
        return self.ckan_get(url)


    def package_register(self, name, title, description="", id="", private=False, author="", author_email="",
                         license_id="cc-by", groups=[], owner_org="", extras={}):
        """
        Generates a CKAN dataset (package), more info:
        https://docs.ckan.org/en/2.9/api/index.html#ckan.logic.action.create.package_create

        :param name:
        :param title:
        :param private:
        :param author:
        :param author_email:
        :param license_id:
        :param groups:
        :param owner_org:
        :return: CKAN's response as JSON dict
        """

        # check if package eixsts
        data = {
            "name": name,
            "title": title,
            "notes": description,
            "owner_org": owner_org,
            "id": id,
            "private": private,
            "author": author,
            "author_email": author_email,
            "groups": groups,
            "license_id": license_id,
            "extras": process_extras(extras)
        }

        self.object_create_or_update("package", data)


    def resource_create(self, package_id, resource_id, description="", name="", upload_file=None, resource_url="",
                        format=""):
        """
        Adds a resource to a dataset
        :param datadict: Dictionary with metadata
        :param dataset_id: ID of the dataset
        :param resource: resource file
        """
        # package_id (string) – id of package that the resource should be added to.
        # url (string) – url of resource
        # description (string) – (optional)
        # format (string) – (optional)
        # hash (string) – (optional)
        # name (string) – (optional)
        # resource_type (string) – (optional)
        # mimetype (string) – (optional)
        # mimetype_inner (string) – (optional)
        # cache_url (string) – (optional)
        # size (int) – (optional)
        # created (iso date string) – (optional)
        # last_modified (iso date string) – (optional)
        # cache_last_updated (iso date string) – (optional)
        # upload (FieldStorage (optional) needs multipart/form-data) – (optional)

        data = {
            "id": resource_id,
            "package_id": package_id,
            "description": description,
            "name": name
        }

        if format:
            data["format"] = format
        elif upload_file:
            data["format"] = upload_file.split(".")[-1]

        if resource_url:
            data["url"] = resource_url

        return self.object_create_or_update("resource", data)


    def check_if_package_exists(self, id):
        """
        Checkds if a pacakge exists
        :param id: package id
        :return:
        """
        return self.check_if_exists("package_show", id)

    def check_if_resource_exists(self, id):
        """
        Checkds if a pacakge exists
        :param id: package id
        :return:
        """
        return self.check_if_exists("resource_show", id)

    def check_if_exists(self, endpoint, id):
        """
        Tries to get an entity to check if it exists or not
        :param endpoint: entity enpoint, e.g. "package_show" for dataset, "resource_show" for resource...
        :param id: id of the entity
        :return: None if it does not exit, otherwise returns the entity
        """
        url = self.url + endpoint
        data = {"id": id}
        try:
            entity = self.ckan_get(url, data=data)
        except ValueError:
            return None

        return entity  # only if it exists!

    def get_resource(self, resource_id):
        url = self.url + "resource_show"
        data = {
            "id": resource_id
        }
        return self.ckan_get(url, data=data)

    # ---------------- ORGANIZATIONS ---------------- #
    def get_organizations_list(self):
        """
        Gets all organizations
        :return:
        """
        url = self.url + "organization_list"
        return self.ckan_get(url)

    def __get_resources(self, packages: list):
        """
        From a list of packages + resources, get only the packages
        """
        resources = []
        for package in packages:
            if "resources" in package.keys():
                resources += package["resources"]
        return resources

    def object_exists(self, obj_type: str, obj: dict) -> (bool, bool):
        """
        Checks if an object exists or not and if needs to be updated
        :param obj_type:
        :param obj:
        :return: exists (T/F), needs to be updated (T/F)
        """

        assert "id" in obj.keys(), f"Object does not have id field!: {json.dumps(obj)}"

        if obj_type in ["package", "resource"]:
            # Packages are slightly different, they also contain the resources and use different parameters
            url = self.url + "current_package_list_with_resources"
            params = {'limit': 10000}  # Adjust as needed
        else:
            url = self.url + f"{obj_type}_list"
            params = {'all_fields': True, "include_extras": True}

        registered = self.ckan_get(url, params)  # get list of registered items

        if isinstance(registered, dict):  # if dict, convert it
            registered = registered["results"]

        # If we are fetching  resources, we need to get them from inside the packages
        elif obj_type == "resource":
            registered = self.__get_resources(registered)

        registered_ids = [r["id"] for r in registered ]

        # Now check if we have an object with the same id
        if obj["id"] not in registered_ids:
            return False, False  # object does not exist!

        # Now we know that the object exists, let's figure out if it needs to be updated
        if updated_required(registered, obj):
            return True, True  # Object exists and requires an update
        else:
            return True, False # Object exists, no update required

    def object_create_or_update(self, obj_type, obj):
        """
        Checks if an object exists and if needs to be updated.
        :param obj_type: CKAN type like organization or group
        :param obj:
        :return:
        """
        exists, update = self.object_exists(obj_type, obj)
        obj_id = obj["id"]

        if not exists:
            self.info(f"CREATE new object      '{obj_type}'  with id='{obj_id}'")
            url = self.url + obj_type + "_create"
            return self.ckan_post(url, obj)

        elif exists and update:
            self.info(f"UPDATE existing object '{obj_type}'  with id='{obj_id}'")
            url = self.url + obj_type + "_create"
            return self.ckan_patch(url, obj)

        elif exists and not update:
            self.info(f"No update required for '{obj_type}'  with id='{obj_id}'")
            return {}

        else:
            self.error("This should never happen!", exception=ValueError)
            return {}

    def organization_create(self, group_id:str, name: str, title:str, description="", image_url="", extras={}):
        """
        Creates a group (project) in CKAN
        +info: https://docs.ckan.org/en/2.9/api/index.html#ckan.logic.action.create.group_create
        :param group_id: group's id
        :param name: group's name
        :param title: group's title
        :param description: grop's description (optional)
        :param image_url: group's image (optinal)
        :param extras: additional key-value pairs
        :param update: if True, instead of post (create) an existing organization will be patched
        :return:
        """
        group_data = {
            "name": name,
            "id": group_id,
            "title": title,
            "description": description,
            "image_url": image_url,
            "extras": process_extras(extras)
        }
        self.object_create_or_update("organization", group_data)

    # ---------------- GROUPS ---------------- #
    def get_group_list(self):
        """
        Gets CKAN's groups
        """
        url = self.url + "group_list"
        return self.ckan_get(url)

    def group_create(self, group_id:str, name: str, title:str, description="", image_url="", extras=[]):
        """
        Creates a group (project) in CKAN
        +info: https://docs.ckan.org/en/2.9/api/index.html#ckan.logic.action.create.group_create
        :param group_id: group's id
        :param name: group's name
        :param title: group's title
        :param description: grop's description (optional)
        :param image_url: group's image (optinal)
        :param extras: additional key-value pair    s
        :return:
        """
        group_data = {
            "name": name,
            "id": group_id,
            "title": title,
            "description": description,
            "image_url": image_url,
            "extras": process_extras(extras)
        }
        return self.object_create_or_update("group", group_data)

    # ---------------- GENERIC METHODS ---------------- #
    def ckan_get(self, url, data={}):
        """
        Get a URL from CKAN
        :param url:
        :param data:
        :return:
        """
        t = time.time()

        # Create the full URL, including params before sending it
        full_url = requests.Request('GET', url, params=data ).prepare().url

        if full_url in self.__cache.keys():
            # We already have the cache in the URL
            cached_time, cached_value = self.__cache[full_url]

            # Check if the cached value is still relevant, or it has timed out
            if time.time()  < cached_time + self.__cache_time:
                return cached_value

        headers = {"Authorization": self.api_key, 'Content-Type': "application/x-www-form-urlencoded"}
        resp = requests.get(url, headers=headers, params=data)
        if resp.status_code > 300:
            raise ValueError(f"CKAN HTTP Error code {resp.status_code}, text: {resp.text}")

        # Cache the result
        cached_time = time.time()
        value = json.loads(resp.text)["result"]
        self.__cache[full_url] = (cached_time, value)
        return value

    def ckan_post(self, url, data, file=None):
        headers = {"Authorization": self.api_key}
        resource = []
        if file:
            resource = [("upload", open(file))]
        else:
            data = json.dumps(data, indent=2)
            headers['Content-Type'] = "application/json"

        resp = requests.post(url, data=data, headers=headers, files=resource)
        if resp.status_code > 300:
            self.error("ERROR processing the following document:")
            d = json.loads(data)
            self.error(json.dumps(d, indent=2))
            self.error(f"{resp.text}", exception=ValueError)
        return json.loads(resp.text)["result"]

    def ckan_patch(self, url, data):
        headers = {"Authorization": self.api_key}
        identifier = data["id"]
        data = json.dumps(data)
        #headers['Content-Type'] = "application/x-www-form-urlencoded"
        headers['Content-Type'] = "application/json"
        resp = requests.post(url + f"?id={identifier}", data=data, headers=headers)
        if resp.status_code > 300:
            self.info(f"[red]{resp.text}")
            self.error(f"{resp.text}", exception=ValueError)
        response = json.loads(resp.text)["result"]
        return response

    # def resource_patch(self, patch_data: dict, id: str):
    #     """
    #     Edit attributes within the patch_data dict. The rest remains unchanged
    #     :param patch_data: dict with data to pach
    #     :param id: package id
    #     :return: dataset dict
    #     """
    #     url = self.url + f"resource_patch"
    #     patch_data["id"] = id
    #     return self.ckan_post(url, patch_data)

    def process_mmapi_dataset(self, dataset_conf: dict, resources: list = []) -> list:
        assert_type(dataset_conf, dict)
        assert_types(resources, [list, type(None)])
        try:
            r = dataset_conf["export"]["ckan"]["resources"]
        except KeyError:
            self.error("exporter/ckan/resources not found in dataset config!", exception=KeyError)

        dataset_id = dataset_conf["#id"]
        ckan_dataset_id = dataset_id.lower()  # CKAN accepts only lower case ids
        dataset_resources = dataset_conf["export"]["ckan"]["resources"]

        # Keep only resources in the resource list
        if resources:
            all_resources = [r["id"] for r in dataset_resources]
            for r in resources:
                assert r in all_resources, f"Dataset resource '{r}' not recognized"

            self.info(f"Keeping the following resources: {resources}")
            dataset_resources = [r for r in dataset_resources if r["id"] in resources]

        # Create a list of resources to be posted to ckan like (resource, link, date_start, date_end)
        # If resource does not have associated dates, just use None)
        ckan_resources = []
        self.info("Preparing list of resources to generate...")
        for resource in dataset_resources:
            resource_id = resource["id"]
            # Process dynamically-linked resources hosted in FileServer
            if resource["link"].startswith("$fileserver"):
                fileserver_resource = self.get_linked_resource_conf(dataset_conf, resource["link"])
                fileserver_resource_id = fileserver_resource['id']
                #   fmt = fileserver_resource["format"]
                query =  f"""
                    select dataset_id, resource_id, data_from, data_to, url, path
                    from {self.mc.dataset_registry_table}
                    where LOWER(dataset_id) = LOWER('{dataset_id}') and LOWER(resource_id) = LOWER('{fileserver_resource_id}'); 
                """
                df = self.mc.db.dataframe_from_query(query)
                if df.empty:
                    self.warning(f"No datasets found in database for dataset_id={dataset_id} and resource_id={fileserver_resource_id}")

                for _, row in df.iterrows():
                    ckan_resources.append((resource, row["url"], row["data_from"], row["data_to"]))

            # Process HTTPS-based resources
            elif resource["link"].startswith("https"):
                # This resource is an absolute link to an external resource
                ckan_resources.append((resource, resource["link"], None, None))

            else:
                self.error("Unimplemented resource type, expected HTTPS link or fileserver reference", exception=ValueError)

        # Now create or update all the resources:
        for resource, link, tstart, tend in ckan_resources:
            self.info(f"===> Processing resource " + CYN + dataset_id + RST +  ":" + WHT + resource['id'] + self.log_colour +  " <=====")
            ckan_resource_id = dataset_id + "_" + resource["id"]
            ckan_resource_id = ckan_resource_id.lower()
            description = resource["description"]
            if tstart and tend:
                # In case we have multiple files for the same resource, append start and end date
                ckan_resource_id += f'_{tstart.strftime("%Y%m%d")}_{tend.strftime("%Y%m%d")}'
                description += f'from {tstart.strftime("%Y-%m-%d")} to {tend.strftime("%Y-%m-%d")}'

            fmt = link.split(".")[-1]

            if fmt.lower() == "nc":
                fmt = "NetCDF"

            r = self.resource_create(
                ckan_dataset_id,
                ckan_resource_id,
                description=description,
                name=resource["title"],
                resource_url=link,
                format=fmt
            )
            self.info(f"Resource {ckan_resource_id} processed")
            if r:
                self.generate_resource_view(dataset_id, ckan_resource_id, link)
        return []

    def generate_resource_view(self, dataset_id: str, resource_id: str, resource_url: str,
                               path="/opt/files/other/dataset_views"):
        assert_type(dataset_id, str)
        assert_type(resource_id, str)
        assert_type(resource_url, str)
        assert_type(path, str)

        self.info(f"Creating dataset view for {dataset_id} {resource_id} ...")
        filename = "./" + resource_url.split("/")[-1]
        extension = filename.lower().split(".")[-1]
        resource_view_file = filename.lower().replace(extension, "jpeg")
        implemented_extensions = ["tif", "csv", "nc", "zip"]

        if extension not in implemented_extensions:
            self.warning(f"Generate Resource View not implemented for extension {extension}")
            return

        download_file(resource_url, filename)
        if extension == "tif":
            self.debug("Converting tif to jpeg...")
            tif_image = Image.open(filename)
            rgb_image = tif_image.convert("RGB")

        elif extension in ["csv", "nc", "zip"]:
            dataset = self.mc.get_document("datasets", dataset_id)
            try:
                dataset_plot = auto_plotter(filename, resource_id, dataset)
            except Exception as e:
                self.error(traceback.format_exc())
                self.error(f"Can't create resource view for {dataset_id}:{resource_id}", exception=ValueError)
                return
            rgb_image = Image.open(dataset_plot).convert("RGB")

        else:
            self.error("This should never happen! Check implemented_extensions and if conditions", exception=ValueError)
        os.remove(filename)
        rgb_image = resize_pic(rgb_image)
        rgb_image.save(resource_view_file, "JPEG")
        view_url = self.fileserver.send_file(self.fileserver.basepath + f"/other/ckan_views/{dataset_id}/{resource_id}",
                                             resource_view_file)
        os.remove(resource_view_file)

        # Now check if we need to register the new view in CKAN. We only need to do it once, since the view png file
        # will be overwritten in the fileserver

        # resource_id (string) – id of the resource
        # title (string) – the title of the view
        # description (string) – a description of the view (optional)
        # view_type (string) – type of view
        # config (JSON string) – options necessary to recreate a view state (optional)
        current_views = self.ckan_get(self.url + "resource_view_list", data={'id': resource_id})
        if len(current_views):
            self.info("View already existing in CKAN, no need to insert it...")
        else:
            self.info("Creating new view")
            self.create_resource_view(
                resource_id,
                f"{dataset_id}-{resource_id}",
                f"JPEG view for {dataset_id} {resource_id}",
                view_url
            )

    def create_resource_view(self, resource_id, title, description, image_url, view_type="image_view"):
        data = {
            "resource_id": resource_id,
            "title": title,
            "description": description,
            "view_type": view_type,
            "image_url": image_url
        }
        url = self.url + "resource_view_create"
        return self.ckan_post(url, data)

    def get_linked_resource_conf(self, dataset_conf: dict, link: str):
        """
        In a linked dataset to $fileserver, get the configuration
        :param dataset_conf:
        :param link:
        :return:
        """
        resource_id = link.split("/")[1]  # skip $
        fileserver_conf = {}
        for fileserver_resource in dataset_conf["export"]["fileserver"]["resources"]:
            if fileserver_resource["id"] == resource_id:
                fileserver_conf = fileserver_resource
                break

        if not fileserver_conf:
            self.error(f"Fileserver conf {link} not found!", exception=LookupError)

        return fileserver_conf