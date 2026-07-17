from google.cloud import asset_v1
from google.api_core import client_options
from google.cloud import resourcemanager_v3 # new api

import re #regex
import json
from datetime import datetime
import os

#Runs local client info 
with open("customer_data/raw/asset_info1.txt", "r", encoding="utf-8") as f:
    raw_data = f.read()

def get_project_id_automatically(project_number):
    """
    Queries Google Cloud's ResourceManager API to dynamically map the 
    numeric project number to its human-readable Project ID.
    """
    try:
        client = resourcemanager_v3.ProjectsClient()        
        request = resourcemanager_v3.GetProjectRequest(name=f"projects/{project_number}")
        project = client.get_project(request=request)
        
        return project.project_id
    except Exception:
        return project_number

# Maps raw GCP Asset API service names to user-friendly keywords
API_TO_KEYWORD_MAP: dict[str, str] = {
    
    # Compute & Containers
    "compute.googleapis.com": "compute engine",
    "container.googleapis.com": "google kubernetes engine",
    "run.googleapis.com": "cloud run",
    "cloudfunctions.googleapis.com": "cloud functions",
    "appengine.googleapis.com": "app engine",
    
    # Storage, Databases & Governance
    "storage.googleapis.com": "cloud storage",
    "storage-api.googleapis.com": "cloud storage api",
    "storage-component.googleapis.com": "cloud storage component",
    "bigquery.googleapis.com": "bigquery",
    "dataplex.googleapis.com": "dataplex (knowledge catalog)", # <-- Added Dataplex
    "sqladmin.googleapis.com": "cloud sql",
    "sql-component.googleapis.com": "cloud sql component",
    "redis.googleapis.com": "memorystore for redis",
    "bigtableadmin.googleapis.com": "cloud bigtable",
    "firestore.googleapis.com": "firestore",
    "datastore.googleapis.com": "datastore",
    
    # Integration, Messaging & Analytics
    "pubsub.googleapis.com": "pub/sub",
    "apigee.googleapis.com": "apigee",
    "dataflow.googleapis.com": "dataflow",
    "composer.googleapis.com": "cloud composer",
    
    # CI/CD & Developer Tools
    "cloudbuild.googleapis.com": "cloud build",
    "artifactregistry.googleapis.com": "artifact registry",
    "containerregistry.googleapis.com": "container registry",
    "dataform.googleapis.com": "dataform",
    "containeranalysis.googleapis.com": "container analysis",
    "staging-containeranalysis.sandbox.googleapis.com": "container analysis (staging)",
    
    # Management & Security
    "serviceusage.googleapis.com": "service usage",
    "cloudresourcemanager.googleapis.com": "cloud resource manager",
    "cloudbilling.googleapis.com": "cloud billing",
    "orgpolicy.googleapis.com": "organization policy",
    "iam.googleapis.com": "identity and access management",
    "iap.googleapis.com": "identity aware proxy",
    "cloudasset.googleapis.com": "cloud asset inventory",
    
    # Operations & AI
    "logging.googleapis.com": "cloud logging",
    "monitoring.googleapis.com": "cloud monitoring",
    "cloudtrace.googleapis.com": "cloud trace",
    "telemetry.googleapis.com": "telemetry",
    "cloudapis.googleapis.com": "google cloud apis",
    "aiplatform.googleapis.com": "vertex ai",
}

pattern = re.compile(r"^\/\/([a-z0-9\-.]+)\.googleapis\.com(?:/projects/([a-zA-Z0-9\-]+))?(?:\/([a-zA-Z0-9\-]+))?")

parsed_assets = []


for line in raw_data.strip().split("\n"):
    # Clean up line: grab just the raw URI portion before the space
    parts = line.strip().split(" ")
    uri = parts[0]
    
    # We also look at the asset type at the end (e.g., 'sqladmin.googleapis.com/Instance') 
    # to help resolve instances where the service in the URI differs from the actual underlying service.
    asset_type = parts[1] if len(parts) > 1 else ""
    
    match = pattern.match(uri)
    if match:
        raw_service = f"{match.group(1)}.googleapis.com"
        project_group = match.group(2)
        fallback_group = match.group(3)
        
        # Decide project context
        project_name = project_group if project_group else fallback_group

        project_name = get_project_id_automatically(project_name)
        
        # Resolve the service keyword. If the asset_type reveals a more specific API (like sqladmin),
        # check that first; otherwise, fall back to the raw service domain.
        service_keyword = "Unknown Service"
        for api_key in API_TO_KEYWORD_MAP:
            if api_key in asset_type or api_key == raw_service:
                service_keyword = API_TO_KEYWORD_MAP[api_key]
                break
                
        parsed_assets.append({
            "project": project_name,
            "service": service_keyword,
            "raw_uri": uri
        })


# Get the current date and time
timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

output_folder = "customer_data/customer_keywords_cleaned"  # Change this to your folder path
base_name = "assets"
extension = ".json"

os.makedirs(output_folder, exist_ok=True)

timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
file_name = f"{base_name}_{timestamp}{extension}"

full_path = os.path.join(output_folder, file_name)

# Export the list to a clean JSON file
with open(full_path, "w") as json_file:
    json.dump(parsed_assets, json_file, indent=4)

print(f"Successfully processed {len(parsed_assets)} entries into '{full_path}'!")

