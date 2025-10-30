import datetime
from unidecode import unidecode
import tkinter as tk
from tkinter import ttk
from gui.object import GenericObject
from gui.dropdownlist import DropDownList
from gui.common_fields import TextField, DateTime
from mmm.common import LoggerSuperclass
from .api_client import MmapiClient
import rich


def filterable_collections(schema, path=""):
    """ gets the elements that can be used for filtering in a schema """
    elements = []

    # If they exist include name, title and / or description
    hardcoded_elements = ["name", "title", "description"]
    if not path: # only in root
        for key in hardcoded_elements:
            rich.print(f"Checking {key}...", end="")
            if key in schema["properties"].keys():
                elements.append(key)
                rich.print(f"[green] adding it! {key}")
            else:
                rich.print(f"[red]ignore")

    for key, value in schema["properties"].items():
        if path:
            cpath = path + "/" + key
        else: # avoid leading /
            cpath = key
        if key.startswith("@"):
            elements.append(cpath)
        elif "oneOf" in schema["properties"][key].keys():
            for one_of in schema["properties"][key]["oneOf"]:
                elements += filterable_collections(one_of, path=cpath)
        elif "properties" in schema["properties"][key]:
            elements += filterable_collections(schema["properties"][key], path=cpath)
    return elements


def compare_nested_element(data, path, value) -> bool:
    if "/" in path:
        # go deeper in the structure
        splits = path.split("/")
        key = splits[0]
        newpath = "/".join(splits[1:])
        if key not in data.keys():
            return False
        return compare_nested_element(data[key], newpath, value)
    else:
        if path not in data.keys():
            return False

        if isinstance(data[path], str):
            if value.lower() in data[path].lower():
                return True
            else:
                return False
        elif isinstance(data[path], list):
            for list_value in data[path]:
                if value.lower() in list_value.lower():
                    return True
            return False
        else:
            raise ValueError(f"Can't compare nested element '{path}' with type {type(data[path])}")


def filter_doc_list(docs, path, value):
    """
    Given a list of documents, return only those which attribute "path" = "value"
    :param docs:
    :param path:
    :param value:
    :return:
    """
    out_docs = {}
    for doc_id, doc in docs.items():
        if compare_nested_element(doc, path, value):
            out_docs[doc_id] = doc

    return out_docs


class DocumentLauncher(LoggerSuperclass):
    """
    This class creates a Window with a list of documents and button to load them

    """
    def __init__(self, name, parent, padx=5, pady=5):
        LoggerSuperclass.__init__(self, parent.logger, name)
        self.info(f"Creating document launcher for '{name}'")
        self.parent = parent  # MmapiGuiClient
        self.name = name
        self.root = tk.Toplevel(parent.root)
        self.root.title(name)
        self.row = 0
        self.elements = {}  # ALL elements will be stored here, key=name value=TK object
        self.padx = padx
        self.pady = pady

        schema = parent.schemas[name]
        self.schema = schema
        self.schema_name = name
        self.doc_ids = parent.api.get_ids(name)

        row = 0

        self.mylabel = ttk.Label(self.root, text=f'{name} documents          ')
        self.mylabel.grid(column=0, row=row, padx=padx, pady=pady)
        row +=1
        myscroll = ttk.Scrollbar(self.root)
        myscroll.grid(column=2, row=row, padx=padx, pady=pady, sticky="ns")

        self.doc_list = tk.Listbox(self.root, yscrollcommand=myscroll.set, width=60)
        for doc_id in self.doc_ids:
            self.doc_list.insert(tk.END, doc_id)

        self.update_document_number(len(self.doc_ids))

        self.doc_list.grid(column=0, row=1, columnspan=2, padx=padx, pady=pady, sticky="nsew")
        myscroll.config(command=self.doc_list.yview)
        row += 1

        # Create filter for every table in the schema like @sensors @stations...
        filters = filterable_collections(schema)
        mylabel2 = ttk.Label(self.root, text='Filters')
        mylabel2.grid(column=0, row=row, padx=padx, pady=pady)
        row += 1
        self.filters = {}
        for filter_path in filters:
            if "@" in filter_path:
                # Add a dropdown list with elements from a collection in MMAPI
                endpoint = filter_path.split("/")[-1]
                element_list = parent.api.get_ids(endpoint)
                filter_drop_down = DropDownList(self.root, filter_path, element_list, row, validate=False)
                self.filters[filter_path] = filter_drop_down
                row += filter_drop_down.rows
                filter_drop_down.combobox.bind("<<ComboboxSelected>>", self.filter_updated_callback)  # Handle selection
                filter_drop_down.combobox.bind("<KeyRelease>", self.filter_updated_callback)  # Handle selection
            else:
                # Add a simple free-text field
                filter_text = TextField(self.root, filter_path, row, validation=False)
                filter_text.entry.bind("<Key>", self.filter_updated_callback)  # Handle selection
                row += filter_text.rows
                self.filters[filter_path] = filter_text

        load_btn = tk.Button(self.root, text="Load")
        load_btn.bind("<Button-1>", self.load_doc_callback)
        load_btn.grid(row=row, column=0, columnspan=3, padx=padx, pady=pady, sticky="nsew")
        row += 1

        load_btn = tk.Button(self.root, text="New")
        load_btn.bind("<Button-1>", self.new_doc_callback)
        load_btn.grid(row=row, column=0, columnspan=3, padx=padx, pady=pady, sticky="nsew")

        self.meta_schema = parent.meta_schema
        self.data_schema = parent.schemas[name]

    def filter_combobox_change(self, event):
        self.filter_updated_callback(event)

    def filter_updated_callback(self, event):
        """Updates documents that match all the filters"""
        all_docs = self.parent.api.get_docs(self.name)
        filtered_docs = all_docs
        for filter_path, filter_cbox in self.filters.items():
            fvalue = filter_cbox.get()  # get the ComboBox value
            if not fvalue:
                continue
            filtered_docs = filter_doc_list(filtered_docs, filter_path, fvalue)
        self.doc_list.delete(0, tk.END)  # Clear existing items
        for doc_id in filtered_docs:
            self.doc_list.insert(tk.END, doc_id)
        self.update_document_number(len(filtered_docs))

    def update_document_number(self, n: int):
        self.mylabel.config(text=f"{self.name} documents ({n})")

    def load_doc_callback(self, event):
        selected_index = self.doc_list.curselection()  # Get selected index
        if selected_index:  # Ensure something is selected
            selected_doc = self.doc_list.get(selected_index[0])  # Get the item at the index
            self.debug(f"Loading document: {selected_doc}")
            doc = self.parent.api.get_doc(self.schema_name, selected_doc)
            popup = DocumentManager(self.name, self.meta_schema, self.schema, self.parent, self.schema_name, self.parent.api, padx=self.padx, pady=self.pady)
            popup.load_btn_callback(doc)
        else:
            self.warning("No document selected!!")

    def new_doc_callback(self, event):
        popup = DocumentManager(self.name, self.meta_schema, self.schema, self.parent, self.schema_name, self.parent.api, padx=self.padx, pady=self.pady)


class BaseElement(LoggerSuperclass):
    def __init__(self, root, name, logger, padx=10, pady=10):
        LoggerSuperclass.__init__(self,  name, logger)
        self.root = tk.Frame(root)
        self.root.grid(column=2, row=1, padx=padx, pady=pady, sticky="nsew")



    def put(self, data):
        self.error("Unimplemented!", exception=ValueError)

    def get(self, data):
        self.error("Unimplemented!", exception=ValueError)


class GuiOperation(BaseElement):
    def __init__(self, root, api, logger):
        BaseElement.__init__(self, root,  "operation", logger)
        self.elements = []
        e = TextField(self.root, "description", 1, frame=True)
        self.elements.append(e)
        e = DateTime("date", self.root, 2, frame=True)
        self.elements.append(e)


class DocumentManager(LoggerSuperclass):
    def __init__(self, name, meta_schema, schema, parent, schema_name, api :MmapiClient ,padx=0, pady=0):
        LoggerSuperclass.__init__(self, parent.logger, name)
        self.root = tk.Toplevel(parent.root)
        gframe = tk.Frame(self.root)
        gframe.grid(column=2, row=1, padx=padx, pady=pady, sticky="nsew")
        self.schema = schema
        self.api = api
        self.schema_name = schema_name

        crow = 0
        self.meta = GenericObject("Metadata", gframe, meta_schema, self.logger, parent.api, crow, 0,
                                  padx=padx, pady=pady, colour="cornsilk2")
        crow += 1


        self.data = GenericObject("Data", gframe, schema, self.logger, parent.api, crow, 0, padx=padx,
                                      pady=pady)
        crow += 1
        val_btn = tk.Button(gframe, text="Validate")
        val_btn.bind("<Button-1>", self.validate_btn_callback)
        val_btn.grid(row=crow, column=0, padx=padx, pady=pady, sticky="nsew")
        self.val_btn = val_btn

        load_btn = tk.Button(gframe, text="Insert")
        load_btn.bind("<Button-1>", self.insert_btn_callback)
        load_btn.grid(row=crow, column=1, padx=padx, pady=pady, sticky="nsew")

        load_btn = tk.Button(gframe, text="Delete")
        load_btn.bind("<Button-1>", self.delete_btn_callback)
        load_btn.grid(row=crow, column=2, padx=padx, pady=pady, sticky="nsew")
        self.root.title(f"New {name}")

    def split_meta(self, doc) -> (dict, dict):
        """
        Splits metadata and data
        :param doc:
        :return: meta, data
        """
        meta = {key: value for key, value in doc.items() if key.startswith("#")}
        data = {key: value for key, value in doc.items() if not key.startswith("#")}
        return meta, data

    def load_btn_callback(self, doc: dict):
        meta, data = self.split_meta(doc)
        self.meta.put(meta)
        self.data.put(data)
        self.root.title(doc["#id"])

    def validate_btn_callback(self, event):
        rich.print(self.schema)
        meta = self.meta.get()
        data = self.data.get()

        # Autofill missing fields
        if not meta["#id"]:
            self.info("Generating automatically an #id for the document")
            meta["#id"] = self.generate_id(self.schema, meta, data)

        if not meta["#version"]:
            meta["#version"] = 1

        if not meta["#creationDate"]:
            meta["#creationDate"] = datetime.datetime.now(datetime.UTC).strftime("%Y-%d-%mT%H:%M:%SZ")
            meta["#modificationDate"] = meta["#creationDate"]

        if not meta["#modificationDate"]:
            meta["#modificationDate"] = datetime.datetime.now(datetime.UTC).strftime("%Y-%d-%mT%H:%M:%SZ")

        self.meta.put(meta)
        doc = self.meta.get()
        doc.update(data)  # add the data
        doc = purge_empty_fields(doc)

        self.info("Validating document...")
        rich.print(f"[RED]DELETING TIME FIELD TO MAKE EVERYTHING FAIL!")
        del doc["time"]
        success, msg = self.api.validate_doc(self.schema_name, doc)
        if success:
            self.val_btn.config(text="Validate ✓", fg="green")
        else:
            self.val_btn.config(text="Validate x", fg="red")
            rich.print(f"[red]{msg}")


        rich.print(doc)

    def insert_btn_callback(self, event):
        doc = self.meta.get()  # get the metadata
        doc.update(self.data.get()) # add the data
        doc = purge_empty_fields(doc)
        rich.print(doc)

    def delete_btn_callback(self, event):
        pass


    def generate_id(self, schema: dict, meta: dict, data: dict)->str:
        if schema["$id"] == "mmm:people":
            iden = data["givenName"] + " " + data["familyName"]
            iden =  iden.lower().replace(" ", "_")
        elif schema["$id"] == "mmm:activities":
            rich.print(data)
            iden = data["givenName"] + " " + data["familyName"]
            iden = iden.lower().replace(" ", "_")
        else:
            self.error(f"Generation of #id not implemented for {schema['#id']}", exception=ValueError)

        return unidecode(iden)


def purge_empty_fields(doc: dict) -> dict:
    doc = doc.copy()
    keys = list(doc.keys())
    values = list(doc.values())
    for key, value in zip(keys, values):
        if not value:
            del doc[key]
        elif isinstance(value, dict):
            doc[key] = purge_empty_fields(doc[key])
    return doc

class MmapiGui(LoggerSuperclass):
    """
    Creates a button for each collection
    """
    def __init__(self, secrets: dict, log):
        LoggerSuperclass.__init__(self, log, "GUI")
        self.info("Init Metadata GUI Client")
        self.api = MmapiClient(secrets, log)
        self.root = tk.Tk()
        self.root.attributes('-alpha', 0.2)  # Set transparency (0.0 = fully transparent, 1.0 = fully opaque)
        self.schemas = self.api.get_schemas()  # schema for all objects
        self.meta_schema = self.api.get_metadata_schemas()  # schema for document metadata
        self.row = 0

        for key, value in self.schemas.items():
            btn = tk.Button(self.root, text=key)
            btn.bind("<Button-1>", self.launch_panel)
            btn.grid(row=self.row, column=0, padx=10, pady=10, sticky="nsew")
            self.row += 1

    def launch_panel(self, event):
        # Get the name of the of the Schema to create a new window
        schema_name = event.widget.cget("text")
        popup = DocumentLauncher(schema_name, self, pady=4)

    def run(self):
        self.root.mainloop()
