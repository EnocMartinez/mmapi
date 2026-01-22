# Metadata API #

This project contains the Metadata API, a RESTful API designed to administrate metadata regarding scientific data and metadata such as observation stations, datasets, operations, etc. In addition to the API a set of scripts to manage data and metadata are also included.

This API is heavily influenced by the SensorThings API, but has some major differences:  

- It adds additional classes such as people, operations, activities, deployments, projects and more.
- It does not manage data, just the metadata
- Instead of using a relational database it relies on a MongoDB to store JSON documents
- Every document is versioned and stored, so the whole lifecycle of a document can be retrieved.


## Metadata API Data Types ##
| data type  | description                                           | data model                         | full data stored in     | averaged data stored in |
|------------|-------------------------------------------------------|------------------------------------|-------------------------|-------------------------|
| timeseries | fixed-point timeseries data                           | time, value(float)                 | `timeseries` hypertable | `OBSERVATIONS` table    |
| profiles   | depth-dependant timeseries data                       | time, depth (float), value (float) | `profiles` hypertable   | `OBSERVATIONS` table    |
| detections | fixed-point counts data (e.g. fish counts)            | time, value(int)                   | `detections` hypertable | n/a                     |
| inference  | complex JSON structures, mainly produced by AI models | time, value(json), params(json)    | `OBSERVATIONS` table    | n/a                     |
| files      | Any file-based data, value should be the file URL     | time, value(str), params(json)     | `OBSERVATIONS` table    | n/a                     |


## Metadata API to SensorThings mappings ##

### Conventions

* MongoDB `#id` fields corresponds to SensorThings `name` field
* SensorThings entities `name` field must be unique for every collection (database unique restriction added)
* For every station, a Thing and a FeatureOfInterest are created, sharing the same name

### Sensors

| MMAPI          | SensorThings                         |
|----------------|--------------------------------------|
| #id            | name                                 |
| description    | description                          |
| instrumentType | properties:instrumentType            |
| model          | properties:model                     |
| variables      | NOT mapped (included as datastreams) |
| contacts       | NOT mapped                           |

## Sensors and Datasets ##
### Data Types ###
Sensors produce data. Datasets compile data from sensors, easy enough. 

Data coming from sensors is archived depending on the Sensor's data type. Available data types are:
* **timeseries**: timeseries data. Stored in `timeseries` hypertable
* **profile**: depth-dependant timeseries. Stored in `profiles` hypertable
* **detections**: number of events detected in a time, e.g. AI-based detections on a picture. Stored in `detections` hypertable
* **files**: File-based sensor. Files can be any multimedia type, such as audio, video, pictures, etc. Each file has an entry in the regular SensorThings `OBSERVATIONS` table 

Trajectory data is stored as regular timeseries data, where the latitude, longitude and depth are treated as timeseries and compiled later.
### Processes ###
Every sensor may have a set of "processes" associated. Currently, the associated processes are:
* **Averaging**: average raw data over a certain period of time. This data will be stored in "OBSERVATIONS".
* **Inference**: Run an inference to the observation (e.g. run an object detection AI job). The data will be stored as parameters in "OBSERVATIONS". The individual detections may also be stored as separated timeseries in "OBSERVATIONS".


### Features Of Interest ###
Features Of Interest (FOIs) are matched to a particular monitoring programme.


# Marine Metadata API #

In marine metadata api (mmapi) metadata schema, there are the following elements:
* **sensors**:  
* **stations**: 
* **variables**: variables that are measured
* **units**: units of measurement
* **people**: Persons
* **organizations**: institutions, companies or other legal entities
* **projects**: Research projects 
* **activities**: An event applied to a sensor/platform at a particular time, e.g. deployment, recovery, maintenance...
* **operations**: Collections of events in a timeframe, e.g. oceanographic campaign


### Contact info ###

* **author**: Enoc Martínez  
* **version**: 0.1.0
* **organization**: Universitat Politècnica de Catalunya (UPC)  
* **contact**: enoc.martinez@upc.edu  


