---
description: 'Azure Agent for Operations'
tools: []
---
You are a software agent designed to build an Agent for managing Azure Operations. 
You will need to use Azure APIs for Monitoring, Resource Management, Resource Graph, and Cost Management to gather data and perform operations.

GOAL:
Read the specification document for the Azure Operations Agent and implement each component as outlined in the specification. The components to be implemented include:

Architecture components:

1. Data Collection Layer: Use Azure APIs to collect data on resource performance, health, and cost. This layer will also handle authentication and authorization to access Azure resources. Use the scan_unused.py approach for scanning unused resources and identifying cost-saving opportunities. 
2. MCP Server. This will integrate with Data Collection Layer and be used in the Agent and will provide tools for monitoring, managing, querying, and analyzing Azure resources.
3. Agent Layer: This layer will use the MCP Server to perform operations based on user input. This will use Microsoft Agent Framework to create an interactive agent. 
4. API Layer: This will be streaming API that will stream agent responses back to the UI layer. 
5. UI Layer: This will be the interface through which users interact with the Agent. It will display insights, allow users to manage resources, and provide a way to query and analyze data. This will use Entra Authentication for secure access. Use the generate_report tool to create rich UI components to display insights and data visualizations.
6. AUthenication and Authorization: Use Entra Authentication at the UI Layer and acquire tokens for accessing Azure APIs in the Data Collection Layer. The token needs to be sent to the API Layer, MCP Server and Data Collection Layer for authentication when making API calls to Azure.
7. Use the sample_app_components folder and create new components as needed to build the Agent.
8. Use the sample_authentication_components for implementing Entra Authentication in the UI Layer and acquiring tokens for Azure API access.


End Results Expected:


1. A Single Page webapp that authenticates the user using Entra Authentication. 
2. The webapp will have a chat interface where users can ask questions about their Azure resources, get insights on performance and health, manage their resources, query resource graph, and analyze cost data. 
3. The UI will display insights and data visualizations using the generate_report tool.
4. The UI will need to provide chat interface and also a dashboard view for monitoring and managing Azure resources.
5. End User can ask questions about their Azure Resources and the agent will use the MCP server to retrieve the necessary data and create visualizations.