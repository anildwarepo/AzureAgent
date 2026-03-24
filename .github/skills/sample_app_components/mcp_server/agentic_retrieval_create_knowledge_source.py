from azure.search.documents.indexes.models import KnowledgeAgent, KnowledgeAgentAzureOpenAIModel, KnowledgeSourceReference, AzureOpenAIVectorizerParameters, KnowledgeAgentOutputConfiguration, KnowledgeAgentOutputConfigurationModality
from azure.search.documents.indexes import SearchIndexClient
from dotenv import load_dotenv
import os
from azure.search.documents.indexes.models import SearchIndexKnowledgeSource, SearchIndexKnowledgeSourceParameters
from azure.search.documents.indexes import SearchIndexClient
from azure.identity import AzureDeveloperCliCredential, DefaultAzureCredential


load_dotenv()
azure_search_credential = AzureDeveloperCliCredential()
knowledge_source_name = "alta-knowledge-source"
knowledge_agent_name = "alta-knowledge-agent"
ks = SearchIndexKnowledgeSource(
    name=knowledge_source_name,
    description="Knowledge source for Earth at night data",
    search_index_parameters=SearchIndexKnowledgeSourceParameters(
        search_index_name=os.environ["AZURE_SEARCH_INDEX"],
        source_data_select="id,para,part_id, summary",
    ),
)

index_client = SearchIndexClient(endpoint=os.environ["AZURE_SEARCH_SERVICE_ENDPOINT"], credential=azure_search_credential)
index_client.create_or_update_knowledge_source(knowledge_source=ks, api_version="2025-08-01-preview")
print(f"Knowledge source '{knowledge_source_name}' created or updated successfully.")


aoai_params = AzureOpenAIVectorizerParameters(
    resource_url=os.environ["AZURE_OPENAI_ENDPOINT"],
    deployment_name=os.environ["AZURE_OPENAI_CHAT_DEPLOYMENT_NAME"],
    model_name=os.environ["AZURE_OPENAI_CHAT_DEPLOYMENT_NAME"],
)

output_cfg = KnowledgeAgentOutputConfiguration(
    modality=KnowledgeAgentOutputConfigurationModality.ANSWER_SYNTHESIS,
    include_activity=True,
)

agent = KnowledgeAgent(
    name=knowledge_agent_name,
    models=[KnowledgeAgentAzureOpenAIModel(azure_open_ai_parameters=aoai_params)],
    knowledge_sources=[
        KnowledgeSourceReference(
            name=knowledge_source_name,
            reranker_threshold=2.5,
        )
    ],
    output_configuration=output_cfg,
)

index_client = SearchIndexClient(endpoint=os.environ["AZURE_SEARCH_SERVICE_ENDPOINT"], credential=azure_search_credential)
index_client.create_or_update_agent(agent, api_version="2025-08-01-preview")
print(f"Knowledge agent '{knowledge_agent_name}' created or updated successfully.")

