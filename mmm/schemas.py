#!/usr/bin/env python3

# Generic metadata for ALL documents
mmm_metadata = {
    "$id": "mmm:document_metadata",
    "type": "object",
    "properties": {
        "#id": {"type": "string"},
        "#version": {"type": "integer"},
        "#creationDate": {"type": "string"},
        "#modificationDate": {"type": "string"},
        "#author": {"type": "string"},
    },
    "required": ["#id", "#version", "#creationDate", "#modificationDate", "#author"]
}

# --------- Generic ----------- #

__string_list__ = {
    "type": "array",
    "minItems": 1,
    "items": {"type": "string"}
}

__label_definition = {
    "type": "object",
    "properties": {
        "definition": {"type": "string"},
        "label": {"type": "string"}
    },
    "required": ["definition", "label"]
}

__coordinates__ = {
    "type": "object",
    "properties": {
        "latitude": {"type": "number"},
        "longitude": {"type": "number"},
        "depth": {"type": "number"}
    },
    "required": ["latitude", "longitude", "depth"]
}

mmapi_data_types = [
    "timeseries",  # fix-point timeseries
    "profiles",  # depth-dependant timeseries. Depth is usually stored as integer to simplify indexing
    # others
    "files",  # arbitrary file-based data, such as audio, pictures or video.
    "detections",  # event detections based from other data. An example is fish detections from a picture
    "json"  # JSOn object, such as the output of an AI algorithm, i.e. a list of detected objects by inference
]

__data_types__ = {
    "type": "string",
    "enum": mmapi_data_types
}

__dataset_data_types__ = {
    "type": "string",
    "enum": mmapi_data_types + ["mixed"]
}


# ----------- Conventions ------------ #
__doi_roles__ = [  # Roles for dataset attribution from MetadataKernel
    "ContactPerson",
    # Person with knowledge of how to access, troubleshoot, or otherwise field issues related to the resource
    "DataCollector",  # Person/institution responsible for finding or gathering/collecting data
    "DataCurator",  # Person tasked with reviewing, enhancing, cleaning, or standardizing metadata
    "DataManager",  # Person responsible for maintaining the finished resource
    "Distributor",
    # Institution tasked with responsibility to generate/disseminate copies of  the resource in either electronic or print form
    "Editor",  # A person who oversees the details related to the publication format of the resource
    "HostingInstitution",
    # Typically, the organisation allowing the resource to be available on the internet through the provision of its hardware/software/operating support     "Researcher", # A person involved in analysing data or the results of an experiment or formal study
    "ProjectLeader",
    # Person officially designated as head of project team or sub-project team instrumental in the work necessary to development of the resource
    "RelatedPerson",
    # A person without a specifically defined role in the development of the resource, but who is someone the author wishes to recognize
    "Researcher",  # A person involved in analysing data or the results of an experiment or formal study.
    "ResearchGroup",
    # Typically refers to a group of individuals with a lab, department, or division that has a specifically defined focus of activity.
    "RightsHolder",
    # Person or institution owning or managing property rights, including intellectual property rights over the resource
    "Other",  # Any person or institution making a significant contribution to the development and/or maintenance of the
    # resource, but whose contribution is not adequately described by any of the other values
]

__operation_roles__ = [
    # People onboard
    "diver",  # Person who participates in a diving operation, may also participate in operations onboard
    "crewMember",  # person who participates in operations in a boat but does not dive
    "captain",  # boat driver

    # land-base roles
    "operator",  # person who operates a machine or process
    "other"  # all others
]

__device_roles__ = [
    "owner",  # institution owning a station/sensor
    "operator",  # person responsible to operate certain station/sensor
    "principalInvestigator",  # PI of a station/sensor
    "dataManager"
]


def __contacts_with_roles__(roles: list):
    # Generate an array of either people or organization with a certain role list, defined as argument
    return {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "@people": {"type": "string"},
                "@organizations": {"type": "string"},
                "role": {
                    "type": "string",
                    "enum": roles
                }
            },
            "oneOf": [
                {"required": ["@people"], "not": {"required": ["@organizations"]}},
                {"required": ["@organizations"], "not": {"required": ["@people"]}},
            ],
            "required": ["role"]
        }
    }


def __people_with_roles__(roles: list):
    # Generate an array of either people or organization with a certain role list, defined as argument
    return {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "@people": {"type": "string"},
                "roles": {
                    "type": "array",
                    "minItems": 1,
                    "$comment": "$style=multicombobox",
                    "items": {
                        "type": "string",
                        "enum": roles
                    }
                },
            },
            "required": ["@people", "roles"]
        }
    }

__activity_type__ = [
    "deployment",   # Deployment of a instrument, platform or resource
    "recovery",     # recovery a previously deployed asset
    "maintenance",  # operation to ensure the proper functionality of an asset, such as replacing broken parts
    "inspection",   # operation to inspect the status of an asset
    "cleaning",     # cleans an asset
    "calibration",
    "test",         # activity to test the proper functionality of a sensor/platform/resource
    "failure",
    "loss",         # equipment is lost
    "other"
]

__operation_type__ = [
    "intervention",  # Surface or underwater intervention into sensor/station/resource
    "cruise",  # several activities carried out during a cruise
    "test",  # test the functionality of a device
    "other"  # any other option
]

__project_types__ = [
    "european",  # Project funded by the European Comission
    "national",  # Project funded by the national science ministry (or similar)
    "contract",  # Project with a company, EU or national project regulations do not apply
    "other"
]

__partnership_types__ = ["coordinator", "participant", "thirdParty", "other", "associatedPartner"]



# Possible values of the status indicator for operations and activities
__status_type__ = [
    "pending",   # Deployment of a instrument, platform or resource
    "planned",     # recovery a previously deployed asset
    "done"  # operation to ensure the proper functionality of an asset, such as replacing broken parts
]

# -----------------------------#
__people = {
    "$id": "mmm:people",
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "givenName": {"type": "string"},
        "familyName": {"type": "string"},
        "orcid": {"type": "string"},
        "email": {"type": "string"},
        "@organizations": {"type": "string"}
    },
    "required": ["name", "givenName", "familyName", "email", "@organizations"]
}

__organizations = {
    "$id": "mmm:organizations",
    "type": "object",
    "properties": {
        "fullName": {"type": "string"},
        "acronym": {"type": "string"},
        "alternativeNames": {
            "type": "array",
            "items": {
                "type": "string"
            }
        },
        "public": {"type": "boolean"},
        "ROR": {"type": "string"},
        "EDMO": {"type": "string"},
        "logoUrl": {"type": "string"}
    },
    "required": ["fullName", "acronym", "alternativeNames", "public"]
}

# ------------------------------------------------- #
# -------------------- Sensors -------------------- #
# ------------------------------------------------- #
# Sensors define every sensor, which variables is
# it measuring and also the processing, which include
# pre-processing (AI-based inference) or post-processing
# (averaged data)

__sensors = {
    "$id": "mmm:sensors",
    "type": "object",
    "properties": {
        "description": {"type": "string"},
        "shortName": {"type": "string"},
        "longName": {"type": "string"},
        "serialNumber": {"type": "string"},
        "instrumentType": __label_definition,
        "model": __label_definition,
        "manufacturer": __label_definition,
        "contacts": __contacts_with_roles__(__device_roles__),
        "variables": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "@variables": {"type": "string"},
                    "@units": {"type": "string"},
                    "@qualityControl": {"type": "string"},
                    "dataType": __data_types__,
                    "technical": {"type": "boolean"}  # define as technical variable, not of interest for datasets
                },
                "required": ["@variables", "@units", "dataType"]
            },
        },
        "processes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "@processes": {"type": "string"},
                    "parameters": {"type": "object"}
                },
                "required": ["parameters", "@processes"]
            }
        },
        "dataMode": {"type": "string", "enum": ["real-time", "delayed"]},
    },
    "required": ["description", "shortName", "longName", "serialNumber", "instrumentType", "model", "manufacturer",
                 "processes", "dataMode"]
}


# Processes are a rather open structure, only type and info are required, but many other can be passes as optional
# parameters.
__processes = {
    "$id": "mmm:processes",
    "type": "object",
    "properties": {
        "type": {"type": "string", "enum": ["average", "json"]},
        "description": {"type": "string"},
        "reference": {"type": "string", "definition": "Public link with process of the "}
    },
    "required": ["type", "description"]
}

__stations = {
    "$id": "mmm:stations",
    "type": "object",
    "properties": {
        "shortName": {"type": "string"},
        "longName": {"type": "string"},
        "platformType": __label_definition,
        "manufacturer": __label_definition,
        "contacts": __contacts_with_roles__(__device_roles__),
        "emsoFacility": {"type": "string"},
        "defaults": {
            "type": "object",
            "properties": {
                "@programmes":  {"type": "string"}
            },
            "required": ["@programmes"]
        }
    },
    "required": ["shortName", "longName", "platformType", "contacts", "defaults"]
}

# Dataset splitting. For very big files daily files are envisioned, for very low-rate
dataset_exporter_periods = [
    "none",     # a single file for all the dataset
    "yearly",   # a file for every year
    "monthly",  # a file every month
    "daily"     # a file every day
]


# Supported dataset formats
dataset_exporter_formats = [
    "netcdf",
    "csv",
    "zip",
    "dwca"
]

# DataExporter Configuration
# It includes the host where to deliver the file, and the export periodicity


ckan_resource = {
    "type": "object",
    "properties": {
        "id": {"type": "string", "definition": "ID to be assigned to the resource"},
        "title": {"type": "string", "definition": "Visible name of the resource"},
        "description": {"type": "string", "definition": "description name of the resource"},
        "link": {"type": "string",
                "definition": "URL of the resource, if set to $fileserver the last resource uploaded to fileserver will be used"},
        "tags": __string_list__ # array of tags to be added to the ckan dataset
    },
    "required": ["id", "title", "description", "link"]
}


fileserver_resource = {
    "type": "object",
    "properties": {
        "id": {"type": "string", "definition": "ID to be assigned to the resource"},
        "path": {"type": "string", "definition": "path in the server filesystem"},
        "format": {"type": "string", "definition": "format of the resource", "enum": dataset_exporter_formats},
        "period": {"type": "string", "definition": "periodicity of the resource (daily, monthly yearly or none).",
                   "enum": dataset_exporter_periods},
        "host": {"type": "string", "definition": "hostname of the fileserver"},
        "dataType": __dataset_data_types__,
        "averagePeriod": {"type": "string", "definition": "For averaged timeseries/profiles, select the average period, e.g. 30min"},
    },
    "required": ["id", "path", "format", "period", "host", "dataType"]
}


erddap_resource = {
    "type": "object",
    "properties": {
        "id": {"type": "string", "description": "ERDDAP datasetID, if not set generic datsaet id will be used", "$comment": "$width=15"},
        "path": {"type": "string", "description": "path where the datasets will be exported", "$comment": "$width=15"},
        "host": {"type": "string", "description": "host where to deliver the file", "$comment": "$width=15"},
        "period": {"type": "string", "enum": dataset_exporter_periods},
        "format": {"type": "string", "enum": dataset_exporter_formats},
        "dataType": __dataset_data_types__,
        "averagePeriod": {"type": "string", "definition": "For averaged timeseries/profiles, select the average period, e.g. 30min"},
    },
    "required": ["id", "path", "host", "period", "format", "dataType"]
}

fileserver_exporter_conf = {
    "type": "object",
    "properties": {
        "resources": {
            "type": "array",
            "minItems": 1,
            "items": fileserver_resource
        }
    },
    "required": ["resources"]
}

erddap_exporter_conf = {
    "type": "object",
    "properties": {
        "resources": {
            "type": "array",
            "minItems": 1,
            "items": erddap_resource
        }
    },
    "required": ["resources"]
}

ckan_exporter_conf = {
    "type": "object",
    "properties": {
        "resources": {
            "type": "array",
            "minItems": 1,
            "items": ckan_resource
        }
    },
    "required": ["resources"]
}

# Dataset services supported by MMAPI
valid_dataset_services = ["fileserver", "erddap", "ckan"]

__datasets = {
    "$id": "mmm:datasets",
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "summary": {"type": "string"},
        "@stations": {"type": "string"},  # only ONE station
        "@sensors": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "string"
            }
        },
        "@variables": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "string",
            }
        },
        "constraints": {  # Constraint the datset to certain conditions, such as depth and/or time
            "type": "object",
            "properties": {
                "timeRange": {"type": "string"},
                "@processes": {"type": "string"},  # used to filter data by applied process
                "fieldOfView": {  # Inteded to select cameras with specific resource withine their field of view
                    "type": "object",
                    "properties": {
                        "@programmes": {"type":  "string"}
                    },
                    "required": ["@programmes"]
                }
            }
        },
        "dataSourceOptions": {
            "type": "object",
            "properties": {
                "fullData": {"type": "boolean"},
                "averagePeriod": {"type": "string"},
                "mergeSensors": {"type": "boolean"},
            }
        },
        "dataMode": {"type": "string", "enum": ["real-time", "delayed", "mixed", "provisional"]},
        "export": {
            "type": "object",
            "properties": {
                "fileserver": fileserver_exporter_conf,
                "ckan": ckan_exporter_conf,
                "erddap": erddap_exporter_conf
            },
            "required": []
        },
        "contacts": __contacts_with_roles__(__doi_roles__),
        "funding": {
            "type": "object",
            "properties": {
                "@projects": {
                    "type": "array",
                    "items": {"type": "string"}
                }
            },
            "required": ["@projects"]
        }
    },
    "required": ["title", "summary", "@stations", "@sensors",  "contacts", "dataSourceOptions", "export"]
}

__activities = {
    "$id": "mmm:activities",
    "type": "object",
    "properties": {
        "description": {"type": "string"},
        "time": {"type": "string", "$comment": "$style=DateTime"},
        "type": {
            "type": "string",
            "enum": __activity_type__
        },
        "status": {
            "type": "string",
            "enum": __status_type__,
        },
        "appliedTo": {
            "oneOf": [
                {"type": "object", "properties": {"@sensors": {"type": "string",  "minLength": 2}}, "required":["@sensors"]},
                {"type": "object", "properties": {"@stations": {"type": "string",  "minLength": 2}}, "required":["@stations"]},
                {"type": "object", "properties": {"@resources": {"type": "string",  "minLength": 2}}, "required":["@resources"]}
            ]
        },
        "fieldOfView": {
            "type": "object",
            # This is used to record a camera looking at a particular position
            "properties": {
                "@programmes": {"type": "string"},
            },
            "required": ["@programmes"]
        },
        "where": {
            "oneOf": [
                {"type": "object", "properties": {"@stations": {"type": "string",  "minLength": 2}}, "required": ["@stations"]},
                {"type": "object", "properties": {"position": __coordinates__}, "required": ["position"]}
            ]
        }
    },
    "required": ["description", "appliedTo", "time", "status"]
}


__operations = {
    "$id": "mmm:operations",
    "type": "object",
    "properties": {
        "description": {"type": "string"},
        "timeRange": {"type": "string"},
        "status": {
            "type": "string",
            "enum": __status_type__,
        },
        "type": {
            "type": "string",
            "enum": __operation_type__
        },
        "participants": __people_with_roles__(__operation_roles__),
        "@activities": {
            "type": "array",
            "minItems": 1,
            "items": {"type": "string"},
            "$comment": "$style=ActivitySelector"  # Force ActivitySelector class
        },
        "@projects": {
            "type": "array",
            "items": {"type": "string"}
        },
        "@resources": __string_list__,
        "links": __string_list__,
        "comment": {"type": "string"}
    },
    "required": ["description", "timeRange", "type", "participants", "@activities", "status"],
}

__variable_types = [
    "environmental",  # Physical or chemical variables measured in the environment e.g. temperature, speed, pH, etc.
    "biodiversity",   # biodiversity variables such as species detections
    "technical"       # technical data e.g. battery voltage, available memory, GPS position, etc.
]

__variables = {
    "$id": "mmm:variable",
    "type": "object",
    "properties": {
        "standard_name": {"type": "string"},  # CF standard name for environmental data, or WoRMS name for biodiversity
        "description": {"type": "string"},
        "definition": {"type": "string"},
        "cf_compliant": {"type": "boolean"},  # compliant with the Climate & Forecast standard
        "type": {"type": "string", "enum": __variable_types},
        "worms_id": {"type": "string"},  # WoRMS name for fish species
        "polar": {  # Used to define a polar variable (e.g. wind speed/wind direction)
            "type": "object",
            "properties": {
                "module": {"type": "string"},
                "angle": {"type": "string"}
            },
            "required": ["module", "angle"]
        }
    },
    "required": ["standard_name", "description", "definition", "cf_compliant", "type"]
}

__unit_type = ["linear", "logarithmic"]

__units = {
    "$id": "mmm:units",
    "type": "object",
    "properties": {
        "name": {"type": "string", "definition": "units name", "$comment": "VOCAB=P06:prefLabel"},
        "symbol": {"type": "string"},
        "definition": {"type": "string"},
        "type": {"type": "string", "enum": __unit_type}
    },
    "required": ["name", "symbol", "definition", "type"]
}

__resource_type = [
    "boat",   # small boat
    "research_vessel",   # large research vessel used in oceanographic cruises
    "vessel_of_opportunity",   # large research vessel used in oceanographic cruises
    "equipment",
    "infrastructure",    # element that is considered an infrastructure, such as a junction box
    "other"       #
]

__resources = {
    "$id": "mmm:resources",
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "description": {"type": "string"},
        "type": {"type": "string", "enum": __resource_type},
    },
    "required": ["name", "description", "type"]
}

__projects = {
    "$id": "mmm:projects",
    "type": "object",
    "properties": {
        "acronym": {"type": "string"},
        "title": {"type": "string"},
        "totalBudget": {"type": "number"},
        "type": {"type": "string", "enum": __project_types__},
        "active": {"type": "boolean"},
        "dateStart": {"type": "string"},
        "dateEnd": {"type": "string"},
        "logoUrl": {"type": "string"},
        # Link to founding entity
        "funding": {
            "type": "object",
            "properties": {
                "@organizations": {"type": "string"},
                "grantId": {"type": "string"},
                "call": {"type": "string"},
                "coordinator": {"type": "string"},
                "partners": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "@organizations": {"type": "string"},
                            "acronym": {"type": "string", "$comment": "$width=20"},
                            "fullName": {"type": "string"},
                            "budget": {"type": "number"},
                            "partnershipType": {"type": "string", "enum": __partnership_types__}
                        },
                        "required": ["acronym", "fullName", "budget", "partnershipType"]
                    }
                },
            },
            "required": ["@organizations", "grantId"]
        },
    },
    "required": ["acronym", "title", "totalBudget", "type", "funding", "dateStart", "dateEnd"]
}


# This class represents scientific experiments / programmes, such as long-term monitoring of a station or specific
# scientific experiments on a particular area. This will be converted as a FeatureOfInterest in SensorThings API, so
# Observations can be queried via programme.
__programmes = {
    "$id": "mmm:resources",
    "type": "object",
    "properties": {
        "description": {"type": "string"},  # description of the experiment
        "@projects": {  # List of projects funding this experiment
            "type": "array",
            "items": {"type": "string"}
        },
        "geoJsonFeature": {  # GeoJson Feature delimiting the area of Interest
            "type": "object"
        }
    },
    "required": ["description", "@projects", "geoJsonFeature"]
}

mmm_schemas = {
    "people": __people,
    "organizations": __organizations,
    "sensors": __sensors,
    "datasets": __datasets,
    "stations": __stations,
    "variables": __variables,
    # "qualityControl": {},
    "units": __units,
    "operations": __operations,
    "activities": __activities,
    "projects": __projects,
    "processes": __processes,
    "resources": __resources,
    "programmes": __programmes
}
