import json
import time
import tkinter as tk

from shapely.speedups import available
from tkcalendar import DateEntry
from datetime import datetime
from tkinter import ttk
from tkinter import *
import tkinter.font as tkFont
import pandas as pd

from mmm.common import LoggerSuperclass
from .constants import WARN, YELLOW, GREEN, RED, valid_time_formats, GREY
import rich


class SimpleTextField:
    def __init__(self, root, row, padx=10, pady=10, width=60):
        self.entry = tk.Entry(root, width=width)
        self.entry.grid(row=row, column=0, padx=padx, pady=pady, sticky=W)
        self.rows = 1

    def get(self):
        return self.entry.get()

    def put(self, value):
        self.entry.delete(0, tk.END)  # Clears the text inside the Entry
        self.entry.insert(0, str(value))

    def destroy(self):
        self.entry.destroy()


class FocusableWidget:
    """Adds focus_in and focus_out methods that can be used"""
    def __init__(self):
        pass

    def focus_in(self, event):
        rich.print(f"Focus in {self.name}")

    def focus_out(self, event):
        rich.print(f"[grey42]Focus out {self.name}")

    def focus_bind(self, widget, inevent="<FocusIn>", outevent="<FocusOut>"):
        widget.bind(inevent, self.focus_in)
        widget.bind(outevent, self.focus_out)


class TextField(FocusableWidget):
    def __init__(self, root, name, row, padx=10, pady=10, width=60, validation="string", frame=False):
        FocusableWidget.__init__(self)
        if frame:
            self.root = tk.Frame(root)
            self.root.grid(columnspan=3, column=0, row=row)
        else:
            self.root = root


        self.name = name

        self.label = tk.Label(root, text=name, bg=root.cget("bg"))
        self.label.grid(row=row, column=0, padx=padx, pady=pady, sticky=E)
        self.entry = tk.Entry(root, width=width)
        self.entry.grid(row=row, column=1, padx=padx, pady=pady, sticky=W)
        self.focus_bind(self.entry)
        #                                     u"\u2713 valid    "
        self.validation = tk.Label(root, text=u"\u26A0          ", fg=YELLOW, bg=root.cget("bg"))
        self.validation.grid(row=row, column=2, padx=padx, pady=pady, sticky=W)

        self.validation_handler = None
        if validation == "string":
            self.validation_handler = self.validate_string
        elif validation in ["date", "datetime", "time", "timestamp"]:
            self.validation_handler = self.validate_datetime
        elif validation == "float":
            self.validation_handler = self.validate_float
        elif validation == "integer":
            self.validation_handler = self.validate_integer

        if self.name == "time":
            self.validation_handler = self.validate_datetime

        self.entry.bind("<KeyRelease>", self.validate)  # Trigger filtering as user types
        self.row = row + 1
        self. rows = 1


    def focus_in(self, event):
        self.entry.config(bg="lightyellow")

    def focus_out(self, event):
        self.entry.config(bg="white")

    def get(self):
        return self.entry.get()

    def put(self, value):
        self.entry.delete(0, tk.END)  # Clears the text inside the Entry
        self.entry.insert(0, str(value))
        self.validate(None)

    def validate(self, event):
        """call validate via the validation handler"""
        if not self.validation_handler:
            self.validation.config(text="")
            return
        validated = self.validation_handler(self.entry.get())
        if validated:
            self.validation.config(text=u"\u2713 valid    ", fg=GREEN)
        else:
            self.validation.config(text=u"\u26A0 not valid", fg=RED)

    def destroy(self):
        self.label.destroy()
        self.entry.destroy()
        self.validation.destroy()


    @staticmethod
    def validate_float(value):
        """will be true if string not empty"""
        try:
            float(value)
        except ValueError:
            return False
        return True

    @staticmethod
    def validate_float(value):
        """will be true if string not empty"""
        try:
            float(value)
        except ValueError:
            return False
        return True

    # Validation methods
    @staticmethod
    def validate_string(value):
        if value:
            return True

    # Validation methods
    @staticmethod
    def validate_integer(value: str):
        return value.isdigit()

    @staticmethod
    def validate_datetime(value):
        t = None
        for time_format in valid_time_formats:
            try:
                t = pd.to_datetime(value, format=time_format)
                break
            except ValueError:
                pass
        return not pd.isnull(t)


class FloatField(TextField):
    def __init__(self, root, label, row, padx=10, pady=10, width=10):
        TextField.__init__(self, root, label, row, padx, pady, width=width, validation="float")

    def get(self):
        try:
            return float(self.entry.get())
        except ValueError:
            return None


class IntegerField(TextField):
    def __init__(self, root, label, row, padx=10, pady=10, width=10):
        TextField.__init__(self, root, label, row, padx, pady, width=width, validation="integer")

    def get(self):  # overload
        try:
            return int(self.entry.get())
        except ValueError:
            return None

class BoolCheckBox:
    def __init__(self, name, root: Frame, row,  padx=10, pady=10):
        self.var = IntVar()
        self.btn = Checkbutton(root, variable=self.var, bg=root.cget("bg"))
        self.btn.grid(column=0, row=row, pady=pady, padx=padx, sticky="e")
        Label(root, text=name, bg=root.cget("bg")).grid(column=1, row=row, pady=pady, padx=padx, sticky=W)
        self.rows =  1

    def get(self):
        return bool(self.var)

    def put(self, value:bool):
        self.var.set(int(value))


class DateTime:
    def __init__(self, root: tk.Frame, name:str, row=0, column=0, columnspan=3):
        self.name = name
        self.root = tk.Frame(root)
        self.root.grid(column=column, row=row, columnspan=columnspan, sticky="w")
        self.rows = 1

        # Label
        tk.Label(self.root, text=name).grid(row=0, column=0, padx=5, pady=5)

        # Date picker
        self.cal = DateEntry(self.root, width=9, background="darkblue", foreground="white", borderwidth=2,
                        date_pattern="yyyy-mm-dd")
        self.cal.grid(row=0, column=1, padx=5, pady=5)

        # Time pickers
        padx = 1
        width = 2

        #self.hour_spin = tk.Spinbox(self.root, from_=0, to=23, width=width, format="%02.0f")
        self.hour_spin = tk.Entry(self.root, width=width)
        self.hour_spin.grid(row=0, column=2, padx=padx, pady=5)
        tk.Label(self.root, text=":").grid(row=0, column=3, padx=0, pady=5)
        #self.minute_spin = tk.Spinbox(self.root, from_=0, to=59, width=width, format="%02.0f")
        self.minute_spin = tk.Entry(self.root, width=width)
        self.minute_spin.grid(row=0, column=4, padx=padx, pady=5)
        tk.Label(self.root, text=":").grid(row=0, column=5, padx=0, pady=5)
        #self.second_spin = tk.Spinbox(self.root, from_=0, to=59, width=width, format="%02.0f")
        self.second_spin = tk.Entry(self.root, width=width)
        self.second_spin.grid(row=0, column=6, padx=padx, pady=5)


    def get(self):
            # Get date from DateEntry
            date = self.cal.get_date()

            # Get time from Spinboxes
            hour = int(self.hour_spin.get())
            minute = int(self.minute_spin.get())
            second = int(self.second_spin.get())

            # Combine into datetime object
            dt = datetime(
                date.year,
                date.month,
                date.day,
                hour,
                minute,
                second
            )
            return {self.name:  dt.strftime("%Y-%m-%dT%H:%M:%SZ")}

    def put(self, date):
        dt = datetime.strptime(date, "%Y-%m-%dT%H:%M:%SZ")
        def put_time(entry, value):
            entry.delete(0, tk.END)  # Clears the text inside the Entry
            entry.insert(0, str(value))

        put_time(self.hour_spin, dt.strftime("%H"))
        put_time(self.minute_spin, dt.strftime("%M"))
        put_time(self.second_spin, dt.strftime("%S"))


class ActivitySelector(LoggerSuperclass):
    def __init__(self, root, name, api, row, logger, padx=10, pady=10, width=60):
        LoggerSuperclass.__init__(self, logger, "ActivitySelector")
        rich.print("Initializing ActivitySelector!")
        self.api = api
        self.root = root
        self.frame = tk.Frame(root)
        self.frame.grid(row=row, column=0, columnspan=3, sticky="nsew")
        msg = ttk.Label(self.frame, wraplength="4i", justify="left", anchor="n", padding=(10, 2, 10, 6), text="Activities")
        msg.grid(column=0, row=0)

        btn = tk.Button(self.frame, text="Edit")
        btn.grid(column=1, row=0,  sticky="nsew")
        btn.bind("<Button-1>", self.edit)


        self.values = []  # document ids
        self.rendered_values = []  # documents converted into columns
        self.headers = ["type", "time", "target", "applied to"]

        self.listbox = MultiColumnListbox(self.frame, self.headers, self.values)
        self.listbox.update_data(self.headers, self.rendered_values, self.values)
        self.rows = 1

    def render(self, doc) -> list:
        """
        Converts a doc_id into readable columns
        :param doc:
        :return:
        """
        self.data = []
        type = doc["type"]
        date = doc["time"].replace("T", " ")
        target = "NOT FOUND"
        applied_to = "NOT FOUND"
        if "@sensors" in doc["appliedTo"].keys():
            target = doc["appliedTo"]["@sensors"]
            applied_to = "sensor"
        elif "@stations" in doc["appliedTo"].keys():
            target = doc["appliedTo"]["@stations"]
            applied_to = "station"
        elif "@resources" in doc["appliedTo"].keys():
            target = doc["appliedTo"]["@resources"]
            applied_to = "resource"
        return type, date, target, applied_to

    def render_docs(self, doc_ids: list):

        self.rendered_values.clear()
        for doc_identifier in doc_ids:
            if isinstance(doc_identifier, str):
                doc = self.api.get_doc("activities", doc_identifier)
            else:
                doc = doc_identifier
            self.rendered_values.append(self.render(doc))
        return self.rendered_values

    def put(self, data):
        self.values = data
        self.render_docs(data)
        self.listbox.update_data(self.headers, self.rendered_values, self.values)

    def get(self):
        return self.values

    def edit(self, event):
        ActivitySelectorEditor(self, "ActivitySelectorEditor", "activities")
        pass


class ActivitySelectorEditor(LoggerSuperclass):
    def __init__(self, parent, name, collection):
        LoggerSuperclass.__init__(self, parent.logger, "ActivitySelectorEditor")
        self.parent = parent
        self.collection = collection

        # Make sure that parent has API, values,
        assert hasattr(parent, "api")
        assert hasattr(parent, "headers") and isinstance(parent.headers, list)
        assert hasattr(parent, "values") and isinstance(parent.values, list)
        assert hasattr(parent, "render") and callable(getattr(parent, "render"))

        self.root = tk.Toplevel(self.parent.root)
        self.label1 = ttk.Label(self.root, wraplength="4i", justify="left", anchor="n", padding=(10, 2, 10, 6),
                        text=f"Selected {collection}")
        self.label1.grid(column=0, row=0, sticky="nsew")

        self.label2 = ttk.Label(self.root, wraplength="4i", justify="left", anchor="n", padding=(10, 2, 10, 6),
                        text=f"Available {collection}")
        self.label2.grid(column=2, row=0, columnspan=3, sticky="nsew")

        self.selected = MultiColumnListbox(self.root, self.parent.headers, self.parent.values, row=1, column=0, columnspan=1)

        btn_frame = tk.Frame(self.root)
        btn_frame.grid(column=1, row=1)

        btn = tk.Button(btn_frame, text="<<")
        btn.grid(column=0, row=0,  sticky="n")
        btn.bind("<Button-1>", self.add)

        btn = tk.Button(btn_frame, text=">>")
        btn.grid(column=0, row=1,  sticky="n")
        btn.bind("<Button-1>", self.delete)
        self.all_docs = self.parent.api.get_docs(collection)   # id<str>: doc<dict>

        self.all_docs_text = {doc_id: json.dumps(doc) for doc_id, doc in self.all_docs.items()}  # id<str>: doc<str>
        self.available_docs = {}  # Docs to be showed in the right panel (unselected)

        # Pre-render all documents
        self.all_docs_rendered  = {key: self.parent.render(doc) for key, doc in self.all_docs.items()}

        self.available_list = MultiColumnListbox(self.root, self.parent.headers, [], row=1, column=2, columnspan=1)

        filter_frame = tk.Frame(self.root)
        filter_frame.grid(column=2, row=2)
        self.filter_text = TextField(filter_frame, "filter", 0, validation=False)
        self.filter_text.entry.bind("<Key>", self.filter_updated_callback)  # Handle selection

        btn = tk.Button(self.root, text="Done")
        btn.grid(column=0, row=5,  sticky="nsew")
        btn.bind("<Button-1>", self.done)

        self.update_doc_lists()


    def filter_updated_callback(self, event):
        self.update_doc_lists()

    def update_doc_lists(self):
        selected = self.parent.get()
        self.available_docs = {}

        text = self.filter_text.get()

        for doc_id, doc in self.all_docs.items():
            if doc_id not in selected:
                if text and text in self.all_docs_text[doc_id]:
                    self.available_docs[doc_id] = doc
                elif not text:
                    self.available_docs[doc_id] = doc

        self.selected.update_data(self.parent.headers, self.parent.rendered_values, self.parent.values)
        # Get the render of all available documents
        available_rendered = [rendered_doc for doc_id, rendered_doc in self.all_docs_rendered.items()
                              if doc_id in self.available_docs.keys()]


        self.available_list.update_data(self.parent.headers, available_rendered, self.available_docs.keys())
        self.label1.config(text=f"Selected Activities ({len(selected)})")
        self.label2.config(text=f"Available Activities ({len(self.available_docs)})")

    def add(self, event):
        new_docs = self.available_list.get_selected()
        selected = self.parent.get() + new_docs
        self.parent.put(selected)
        self.update_doc_lists()



    def delete(self, event):
        selected = self.selected.get_selected()
        new_values = []
        for i, value in enumerate(self.parent.values):
            if i not in selected:
                new_values.append(value)
        self.parent.put(new_values)
        self.update_doc_lists()

    def done(self, event):
        self.root.destroy()



class MultiColumnListbox(object):
    """use a ttk.TreeView as a multicolumn ListBox"""

    def __init__(self, root, headers, elements, row=1, column=0, columnspan=3):
        self.tree = None
        self.root = root

        self.headers = headers
        self.elements = elements
        self.values = []

        container = ttk.Frame(self.root)
        container.grid(column=column, row=row, columnspan=columnspan)
        # create a treeview with dual scrollbars
        self.tree = ttk.Treeview(container, columns=self.headers, show="headings")
        vsb = ttk.Scrollbar(container, orient="vertical", command=self.tree.yview)
        hsb = ttk.Scrollbar(container, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self.tree.grid(column=1, row=1, sticky='nsew', in_=container)
        vsb.grid(column=2, row=1, sticky='ns', in_=container)
        hsb.grid(column=1, row=2, sticky='ew', in_=container)

        for col in self.headers:
            self.tree.heading(col, text=col.title(), command=lambda c=col: sorty_by(self.tree, c, 0))
            # adjust the column's width to the header string
            self.tree.column(col, width=tkFont.Font().measure(col.title()))


    def update_data(self, headers: list, elements: list, values:list):
        t = time.time()
        self.headers = headers
        self.elements = elements
        self.values = values
        self._build_tree()

    def _build_tree(self):
        #self.tree.delete(*self.tree.get_children())  # delete current
        current_identifiers = self.tree.get_children()

        for item, identifier in zip(self.elements, self.values):
            # Add new elements
            if identifier not in current_identifiers:
                self.tree.insert('', 'end', iid=identifier, values=item)
                # adjust column's width if necessary to fit each value
                for ix, val in enumerate(item):
                    col_w = tkFont.Font().measure(val)
                    if self.tree.column(self.headers[ix], width=None) < col_w:
                        self.tree.column(self.headers[ix], width=col_w)

        for item in list(current_identifiers):
            # Remove elements
            if item not in self.values:
                self.tree.delete(item)


    def get_selected(self) -> list:
        """returns a list with indexes selected"""
        items = self.tree.selection()  # returns a tuple of selected item IDs
        return list(items)


def sorty_by(tree, col, descending):
    """sort tree contents when a column header is clicked on"""
    # grab values to sort
    data = [(tree.set(child, col), child) for child in tree.get_children('')]
    # if the data to be sorted is numeric change to float
    # now sort the data in place
    data.sort(reverse=descending)
    for ix, item in enumerate(data):
        tree.move(item[1], '', ix)
    # switch the heading so it will sort in the opposite direction
    tree.heading(col, command=lambda col=col: sorty_by(tree, col, int(not descending)))
