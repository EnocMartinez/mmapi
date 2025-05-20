#!/usr/bin/env python3
"""
CKAN API client to publish/update datasets and dataset metadata

author: Enoc Martínez
institution: Universitat Politècnica de Catalunya (UPC)
email: enoc.martinez@upc.edu
license: MIT
created: 23/3/21
"""

import requests
import json
import rich
from mmm.common import normalize_string, LoggerSuperclass, PRL, assert_type, download_file, run_over_ssh, check_url, \
    file_list
from mmm.dataset import generate_dataset_resource_id
from mmm.fileserver import FileServer
from mmm import MetadataCollector
from PIL import Image
import logging
import os
from mmm.plotter import auto_plotter


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
        processed.append({"key": key, "value": value})
    return processed


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

    def upload_data_link(self, dataset_id, name, description, link):
        return self.resource_create(dataset_id, name.lower() + "_csv_data", name=name, description=description,
                                      resource_url=link, format="csv")

    def get_organization_list(self):
        """
        Get the organizations in the CKAN
        :return: list of dict's with organizations
        """
        url = self.url + "organization_list"
        return self.ckan_get(url)

    def get_license_list(self):
        url = self.url + "license_list"
        return self.ckan_get(url)

    # --------- Packages (Datasets) ---------#
    def get_packages(self) -> list:
        """
        Get packages with their resources
        """
        url = self.url + "current_package_list_with_resources"
        return self.ckan_get(url)

    def get_package_list(self) -> list:
        """
        Get only the list of current packages
        """
        url = self.url + "package_list"
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
        action = "create"
        package_id = normalize_string(id)
        registered_packages = self.get_package_list()
        registered_packages = [p.replace("-", "_") for p in registered_packages]

        if package_id in registered_packages:
            rich.print(f"[cyan]Dataset '{package_id}' already registered, patching")
            action = "patch"
        else:
            rich.print(f"[green]Creating new dataset '{package_id}'")

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

        if action == "patch":
            rich.print(f"Package {package_id} already exists, patching...")
            url = self.url + f"package_patch"
            return self.ckan_patch(url, data)
        else:
            rich.print(f"Registering {package_id}...")
            url = self.url + "package_create"
            return self.ckan_post(url, data)

    # def package_patch(self, patch_data: dict, id: str):
    #     """
    #     Edit attributes within the patch_data dict. The rest remains unchanged
    #     :param patch_data: dict with data to pach
    #     :param id: package id
    #     :return: dataset dict
    #     """
    #     url = self.url + f"package_patch"
    #     patch_data["id"] = id
    #     return self.ckan_post(url, patch_data)

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

        if self.check_if_resource_exists(resource_id):
            self.warning(f"Resource {resource_id} already exists, patching")
            action = "patch"
        else:
            action = "post"

        self.info(f"{action.upper() + 'ing'} resource '{resource_id}' to package '{package_id}' with format {format}")

        datadict = {
            "id": resource_id,
            "package_id": package_id,
            "description": description,
            "name": name
        }

        if format:
            datadict["format"] = format
        elif upload_file:
            datadict["format"] = upload_file.split(".")[-1]

        if resource_url:
            datadict["url"] = resource_url

        if action == "patch":
            rich.print(f"resource {resource_id} already exists, patching...")
            url = self.url + f"resource_patch"
            return self.ckan_patch(url, datadict)
        else:
            rich.print(f"Registering new resource '{resource_id}'...")
            url = self.url + "resource_create"
            return self.ckan_post(url, datadict)


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

    def organization_create(self, group_id:str, name: str, title:str, description="", image_url="", extras=[], update=False):
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

        url = self.url + "organization_create"
        if update:
            return self.ckan_patch(url, group_data)
        return self.ckan_post(url, group_data)

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
        :param extras: additional key-value pairs
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

        url = self.url + "group_create"
        self.info(f"Creating CKAN group '{name}'")
        return self.ckan_post(url, group_data)

    # ---------------- GENERIC METHODS ---------------- #
    def ckan_get(self, url, data={}):
        headers = {"Authorization": self.api_key, 'Content-Type': "application/x-www-form-urlencoded"}
        resp = requests.get(url, headers=headers, params=data)
        if resp.status_code > 300:
            raise ValueError(f"CKAN HTTP Error code {resp.status_code}, text: {resp.text}")
        return json.loads(resp.text)["result"]

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
            rich.print(f"[red]{json.dumps(json.loads(resp.text), indent=2)}")
            raise ValueError(f"CKAN HTTP Error code {resp.status_code}")
        return json.loads(resp.text)["result"]

    def ckan_patch(self, url, data):
        headers = {"Authorization": self.api_key}
        identifier = data["id"]
        data = json.dumps(data)
        #headers['Content-Type'] = "application/x-www-form-urlencoded"
        headers['Content-Type'] = "application/json"
        resp = requests.post(url + f"?id={identifier}", data=data, headers=headers)
        if resp.status_code > 300:
            rich.print(f"[red]{resp.text}")
            raise ValueError(f"CKAN HTTP Error code {resp.status_code}")
        response = json.loads(resp.text)["result"]
        return response

    def resource_patch(self, patch_data: dict, id: str):
        """
        Edit attributes within the patch_data dict. The rest remains unchanged
        :param patch_data: dict with data to pach
        :param id: package id
        :return: dataset dict
        """
        url = self.url + f"resource_patch"
        patch_data["id"] = id
        return self.ckan_post(url, patch_data)

    def process_mmapi_dataset(self, dataset_conf: dict, datasets={}, fmt="") -> list:
        """
        Processes a dataset configured in MMAPI. The CKAN package will point to an existing file in the fileserver (or
        any other location). There are two options:
            1) Point to a fixed resource with an uri
            2) Reference a dataset in the fileserver
        For option 1) this configuration can be used by adding a http link to the "link" field
            {
              "link": https://link.to.the/resource.csv
              "name": "optical mosaic",
              "description": "geo-referenced optical mosaic in tif format",
              "resource_id": "plome_deep_mission_01_optical_mosaic"
            },

        For option 2) a special string as to be put in link, following a $fileserver/<resource_id> pattern, like
            {
              "link": "$fileserver/navigation",
              "name": "Girona500 navigation with pictures",
              "description": "Girona500 navigation with pictures",
              "resource_id": "plome_deep_mission_01_navigation"
            }
        Configuration will check for a dataset in fileserver section and try to guess the file by running an ls
        command via ssh. The previous example was pointing to this fileserver configuration:
            {
              "host": "plometest",
              "path": "/opt/files/datasets/PLOME/PLOME_DEEP_mission_01",
              "format": "csv",
              "period": "none",
              "resource_id": "navigation"
            }
        """
        assert_type(dataset_conf, dict)
        resources = []
        try:
            resources = dataset_conf["export"]["ckan"]["resources"]
        except KeyError:
            self.error("exporter/ckan/resources not found in dataset config!", exception=KeyError)

        dataset_id = dataset_conf["#id"].lower()

        for resource in resources:
            self.info(f"Registering resource {resource['title']}")
            # Check if url references a previous dataset
            if resource["link"].startswith("$fileserver"):
                self.info(f"Trying to guess a dataset file stored at fileserver")
                # The reference should be $fileserver/<resource_id>, e.g. $fileserver/navigation
                resource_id = resource["link"].split("/")[1]  # skip $
                fileserver_conf = {}
                for fileserver_resource in dataset_conf["export"]["fileserver"]["resources"]:
                    if fileserver_resource["resource_id"] == resource_id:
                        fileserver_conf = fileserver_resource
                        break

                if not fileserver_conf:
                    self.error(f"Fileserver conf {resource['link']} not found!", exception=LookupError)
                # Now try to guess which file it is via ls through ssh

                path = fileserver_conf["path"]

                if not fmt:
                    fmt = fileserver_conf["format"]

                extension = fmt

                # In some cases the extension does not match tne format, process it
                if extension.lower() == "netcdf":
                    extension = "nc"

                self.info(f"Getting number of files in {self.fileserver.host}:{path} with extension {extension}")
                df = self.mc.db.dataframe_from_query(f"""
                    select * from {self.mc.dataset_registry_table} where dataset_id = '{dataset_id}'; 
                """)
                for idx, row in df.iterrows():

                    if not row["path"].endswith(extension):
                        # ignore extensions that do not match
                        continue
                    start = row["data_from"].strftime("%Y-%m-%d")
                    end = row["data_to"].strftime("%Y-%m-%d")
                    self.resource_create(
                        dataset_id,
                        row["resource_id"],
                        description=resource["description"] + f" from {start} to {end}",
                        name=resource["title"],
                        resource_url=row["path"],
                        format=fmt
                    )
                    self.generate_dataset_preview(dataset_id, row["resource_id"], row["url"])
        return []

    def generate_dataset_preview(self, dataset_id: str, resource_id: str, resource_url: str,
                                 path="/opt/files/other/dataset_views"):

        assert_type(dataset_id, str)
        assert_type(resource_id, str)
        assert_type(resource_url, str)
        assert_type(path, str)

        self.info(f"Registering dataset view for {dataset_id} resource {resource_id}")
        self.info(f"    dataset_id={dataset_id}")
        self.info(f"    resource_id={resource_id}")
        self.info(f"    resource_url={resource_url}")
        filename = "./" + resource_url.split("/")[-1]
        extension = filename.lower().split(".")[-1]
        resource_view_file = filename.lower().replace(extension, "jpeg")
        implemented_extensions = ["tif", "csv", "nc"]

        if extension not in implemented_extensions:
            self.warning(f"Generate Resource View not implemented for extension {extension}")
            return

        download_file(resource_url, filename)
        if extension == "tif":
            self.info("Converting tif to jpeg...")
            tif_image = Image.open(filename)
            rgb_image = tif_image.convert("RGB")

        elif extension in ["csv", "nc"]:
            dataset_plot = auto_plotter(filename, dataset_id)
            rgb_image = Image.open(dataset_plot).convert("RGB")

        else:
            self.error("This should never happen! Check implemented_extensions and if conditions", exception=ValueError)

        rgb_image = resize_pic(rgb_image)
        rgb_image.save(resource_view_file, "JPEG")

        view_url = self.fileserver.send_file(self.fileserver.basepath + f"/other/ckan_views/{dataset_id}/{resource_id}",
                                             resource_view_file)

        self.info(f"View created at {view_url}")
        self.info(f"Posting {view_url} as resource view")

        # resource_id (string) – id of the resource
        # title (string) – the title of the view
        # description (string) – a description of the view (optional)
        # view_type (string) – type of view
        # config (JSON string) – options necessary to recreate a view state (optional)

        self.create_resource_view(
            resource_id,
            f"{dataset_id}-{resource_id}",
            f"JPEG view for {dataset_id} {resource_id}",
            view_url
        )

        os.remove(resource_view_file)

    def create_resource_view(self, resource_id, title, description, image_url, view_type="image_view"):
        d = {
            "resource_id": resource_id,
            "title": title,
            "description": description,
            "view_type": "image_view",
            "image_url": image_url
        }

        # Get existing view for this resource

        resp = self.ckan_get(self.url + "resource_view_list", {"id": resource_id})
        action = "post"
        for view in resp:
            if view["title"] == title:
                d["id"] = view["id"]
                action = "patch"
                break

        if action == "post":
            # Create a new view
            self.ckan_post(self.url + "resource_view_create", d)

        elif action == "patch":
            # Patch the view!
            self.ckan_patch(self.url + "resource_view_update", d)
