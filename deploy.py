import vertexai
from vertexai import agent_engines
from chatbot.john import create_agent_app

vertexai.init(
    project="sprinternship-bld-2026",
    location="us-central1",                       # Agent Runtime region, not 'global'
    staging_bucket="gs://john-staging",
)

remote = agent_engines.create(
    agent_engine=create_agent_app(),
    requirements=[
        "google-cloud-aiplatform[adk,agent_engines]",
        "google-cloud-bigquery",
        "requests",
    ],
    extra_packages=["chatbot"],
    env_vars={"DATA_SOURCE": "bigquery"},
)
print(remote.resource_name)
