from azure.search.documents.agent import KnowledgeAgentRetrievalClient
from azure.search.documents.agent.models import ( 
    KnowledgeAgentRetrievalRequest, 
    KnowledgeAgentMessage,
    KnowledgeAgentMessageTextContent, 
    SearchIndexKnowledgeSourceParams,
)

from dotenv import load_dotenv
import os
from azure.identity import AzureDeveloperCliCredential, DefaultAzureCredential
import textwrap
import json

load_dotenv()

knowledge_source_name = "alta-knowledge-source"
knowledge_agent_name = "alta-knowledge-agent"
azure_search_credential = AzureDeveloperCliCredential()
agent_client = KnowledgeAgentRetrievalClient(endpoint=os.environ["AZURE_SEARCH_SERVICE_ENDPOINT"], agent_name=knowledge_agent_name, 
                                             credential=azure_search_credential)
query_1 = """
    how to configure backup policy for oracle on windows server?
    """


search_api_version = "2025-08-01-preview"


async def search_knowledge_base(search_query: str) -> dict:
    """Search the knowledge base for relevant information."""
    messages = []
    messages.append({
        "role": "user",
        "content": search_query
    })

    req = KnowledgeAgentRetrievalRequest(
        messages=[
            KnowledgeAgentMessage(
                role=m["role"],
                content=[KnowledgeAgentMessageTextContent(text=m["content"])]
            ) for m in messages if m["role"] != "system"
        ],
        knowledge_source_params=[
            SearchIndexKnowledgeSourceParams(
                knowledge_source_name=knowledge_source_name,
                kind="searchIndex"
            )
        ],
        
    )

    result = agent_client.retrieve(retrieval_request=req, api_version=search_api_version, )
    print(f"Retrieved content from '{knowledge_source_name}' successfully.")

    print("Response")
    print(textwrap.fill(result.response[0].content[0].text, width=120))

    print("Activity")
    print(json.dumps([a.as_dict() for a in result.activity], indent=2))

    print("Results")
    print(json.dumps([r.as_dict() for r in result.references], indent=2))

    return {"results" : {"response" : result.response[0].content[0].text}
            , "activity": [a.as_dict() for a in result.activity]
            , "references": [r.as_dict() for r in result.references]
            }

if __name__ == "__main__":
    import asyncio
    asyncio.run(search_knowledge_base(query_1))