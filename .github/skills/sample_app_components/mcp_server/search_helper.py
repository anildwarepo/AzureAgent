import os
from typing import List

from azure.core.credentials import AzureKeyCredential
from azure.identity import AzureDeveloperCliCredential, DefaultAzureCredential
from azure.search.documents import SearchClient
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.indexes.models import SearchFieldDataType
from azure.search.documents.models import VectorizableTextQuery
from dotenv import load_dotenv
from user_defined_types import *

load_dotenv()


azure_search_endpoint = os.environ["AZURE_SEARCH_SERVICE_ENDPOINT"]
#credential = AzureKeyCredential(os.getenv("AZURE_SEARCH_ADMIN_KEY", "")) if len(os.getenv("AZURE_SEARCH_ADMIN_KEY", "")) > 0 else DefaultAzureCredential()
azure_search_credential = AzureDeveloperCliCredential()
index_name = os.environ.get("AZURE_SEARCH_INDEX", "some_index")

def get_index_fields(index_name):
    index_client = SearchIndexClient(
        endpoint=azure_search_endpoint, credential=azure_search_credential)
    idx = index_client.get_index(index_name)
    select_fields = []
    vector_fields =  []
    for field in idx.fields:
        #print(field.name)
        if(field.type == SearchFieldDataType.String):
            select_fields.append(field.name)
        if(str.find(field.name, "Vector") > 0):
            vector_fields.append(field.name)
    return select_fields, vector_fields


async def retrieve_search_results(search_query: str, top_k: int = 10) -> List[SearchResult]:
    select_fields, vector_fields = get_index_fields(index_name)  
    search_client = SearchClient(endpoint=azure_search_endpoint, index_name=index_name, credential=azure_search_credential)
    #vector_query = VectorizableTextQuery(text=search_query, k_nearest_neighbors=3, fields=search_fields, exhaustive=True)
  
    vector_queries  = [VectorizableTextQuery(text=search_query, k_nearest_neighbors=top_k, fields=field, exhaustive=True) for field in vector_fields]
    results = search_client.search(  
        search_text=search_query,  
        vector_queries= vector_queries,
        select=select_fields,
        top=top_k
    )  

    search_results: List[SearchResult] = []
    for result in results:
        single_result = {}
        for field in select_fields:
            single_result[field] = result.get(field)
        search_results.append(SearchResult(**single_result))
    return search_results


if __name__ == "__main__":
    import asyncio
    
    search_query = "how to open dispute?"
    top_k = 5
    results = asyncio.run(retrieve_search_results(search_query, top_k))
    print(results)