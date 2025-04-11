import os
import asyncio
import logging
import json
from typing import Any

import streamlit as st

from azure.identity.aio import DefaultAzureCredential  
from dotenv import load_dotenv
from semantic_kernel.agents.azure_ai.azure_ai_agent import AzureAIAgent

# logging
logging.basicConfig(
    level=logging.WARNING,  
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)

# environment variables
load_dotenv()

PROJECT_CONNECTION_STRING = os.environ.get("AZURE_AI_AGENT_PROJECT_CONNECTION_STRING")
MODEL_DEPLOYMENT_NAME = os.environ.get("AZURE_AI_AGENT_MODEL_DEPLOYMENT_NAME")

# Query to sent to claimhandler (this can be changed via the text area)
CLAIM_QUERY = (
    "A customer holding Policy 2 is requesting damages of 4000 euros for claim type B. "
    "The customer states that these costs are due to being rear ended with their car, requiring "
    "costs to replace the rear bumper for their car."
)
# Included in query but hidden, the claimhandler output is a specific JSON structure

JSON_FORMAT = (
    "\nPlease provide a valid JSON response (no markdown formatting, just JSON) with the following structure:\n"
    '{\n'
    '  "policy_assessor": {\n'
    '    "policy_number": "value",\n'
    '    "claim_type": "value",\n'
    '    "damage_claim": "value"\n'
    '  },\n'
    '  "fraud_detector": {\n'
    '    "claim_description": "value"\n'
    '  }\n'
    '}'
)

# define agents , sent query to claimhandler
AGENTS_AND_QUERIES = {
    "ClaimHandlerAgent": {
        "id": "asst_8WPO5fEmxYN96N2yAoIjbrDb"
    },
    "PolicyAssessorAgent": {
        "id": "asst_eUiaOd5KjushG1semhHqtZtX"
    },
    "FraudDetectionAgent": {
        "id": "asst_6dHVA9CV9BH3NAlootUQoj23"
    },
    "ClaimEvaluatorAgent": {
        "id": "asst_k2syPJidqJUINBz7g1jvzssN"
    }
}

if not (PROJECT_CONNECTION_STRING and MODEL_DEPLOYMENT_NAME and AGENTS_AND_QUERIES):
    logging.error("Missing required configuration.")
    st.error("Missing required configuration.")
    st.stop()

#invoke agents
async def invoke_agent(agent: AzureAIAgent, query: str) -> str:
    try:
        #get_response from sk has built-in threading
        response = await agent.get_response(messages=query)
        if isinstance(response, tuple):
            response = response[0]
        return response
    except Exception as e:
        logging.exception("Error invoking agent.")
        return "Error invoking agent."

#run agents 
async def process_claim_run_agents(custom_claim_query: str):
    results = {}
    try:
        # Append JSON formatting instructions to the final query to the agents.
        full_claim_query = custom_claim_query + JSON_FORMAT

        async with DefaultAzureCredential() as creds:
            async with AzureAIAgent.create_client(credential=creds, conn_str=PROJECT_CONNECTION_STRING) as client:
                
                #   ClaimHandlerAgent

                 #get agent from azure foundry
                claim_handler_def = await client.agents.get_agent(AGENTS_AND_QUERIES["ClaimHandlerAgent"]["id"])
                claim_handler = AzureAIAgent(client=client, definition=claim_handler_def)
                claim_handler_query = full_claim_query
          
                structured_claim_json = await invoke_agent(claim_handler, claim_handler_query)
                results["ClaimHandlerAgent Output"] = structured_claim_json
            
                
                # convert response to string , parse as JSON.
                try:
                    structured_claim_str = str(structured_claim_json)
                    parsed_claim = json.loads(structured_claim_str)
                except json.JSONDecodeError:
                    logging.exception("Failed to parse JSON output from ClaimHandlerAgent. Attempting clarification...")
                    clarification_query = (
                        "The output provided does not appear to be valid JSON."
                        "Please provide a valid JSON response following the specified structure."
                    )
                    clarification_output = await invoke_agent(claim_handler, clarification_query)
                    try:
                        structured_claim_str = str(clarification_output)
                        parsed_claim = json.loads(structured_claim_str)
                    except json.JSONDecodeError:
                        logging.exception("Clarification attempt failed, stop process.")
                        results["error"] = "Clarification attempt failed, stop process."
                        return results

                results["Parsed Claim"] = parsed_claim

                # Policy agent

                #retrieve output from claimhandler
                policy_number = parsed_claim.get('policy_assessor', {}).get('policy_number')
                claim_type = parsed_claim.get('policy_assessor', {}).get('claim_type')
                damage_claim = parsed_claim.get('policy_assessor', {}).get('damage_claim')
                
                policy_query = (
                    f"Based on the claim details: policy number {policy_number}, claim type {claim_type}, and damage claim amount {damage_claim} euros, "
                    "determine if the customer is appropriately covered under their policy."
                )
                #get agent from azure foundry
                define_policy_agent = await client.agents.get_agent(AGENTS_AND_QUERIES["PolicyAssessorAgent"]["id"])
                policy_agent = AzureAIAgent(client=client, definition=define_policy_agent)
                st.write("\nSending query to PolicyAssessorAgent...")
                #sent query
                policy_assessment = await invoke_agent(policy_agent, policy_query)
                results["PolicyAssessorAgent Output"] = policy_assessment
            
                
                 #  FraudDetectionAgent 

                 #retrieve output from claimhandler
                claim_description = parsed_claim.get('fraud_detector', {}).get('claim_description')
                
                fraud_query = (
                    f"Using the following claim description: {claim_description}, "
                    "evaluate whether there are any red flags or suspicious elements in the claim."
                )
                #get agent from azure foundry
                define_fraud_agent = await client.agents.get_agent(AGENTS_AND_QUERIES["FraudDetectionAgent"]["id"])
                fraud_agent = AzureAIAgent(client=client, definition=define_fraud_agent)
                st.write("\nSending query to FraudDetectionAgent...")
                #sent query
                fraud_assessment = await invoke_agent(fraud_agent, fraud_query)
                results["FraudDetectionAgent Output"] = fraud_assessment
               
                
                #  evaluatoragent 
                #include policy and fraud output in input of claim_evaluator_agent
                evaluator_query = (
                    f"Policy Assessment: {policy_assessment}\n"
                    f"Fraud Assessment: {fraud_assessment}\n"
                    "Based on the above assessments, should the claim be approved or rejected? Provide a concise explanation."
                )
                #get agent from azure foundry
                define_claim_evaluator_agent = await client.agents.get_agent(AGENTS_AND_QUERIES["ClaimEvaluatorAgent"]["id"])
                claim_evaluator_agent = AzureAIAgent(client=client, definition=define_claim_evaluator_agent)
                st.write("\nSending query to ClaimEvaluatorAgent:")
                claim_evaluator_assesment = await invoke_agent(claim_evaluator_agent, evaluator_query)
                results["ClaimEvaluatorAgent Output"] = claim_evaluator_assesment
             
    except Exception as e:
        logging.exception("An unexpected error occurred .")
        results["error"] = str(e)
    return results

st.title("Insurance claim dashboard")
st.write("Enter your claim query below and click the button to process the claim using Azure AI Agents.")

# text area 
user_claim_query = st.text_area("Query:", value=CLAIM_QUERY, height=150)

if st.button("Process Claim"):
    # streamlit spinner 
    with st.spinner("Processing the claim..."):
        results = asyncio.run(process_claim_run_agents(user_claim_query))
    if "error" in results:
        st.error("An error occurred: " + results["error"])
    else:
        st.success("Processing complete!")
        st.header("Summary", divider="gray")
        st.subheader("Policy Agent Output")
        st.code(results.get("PolicyAssessorAgent Output", "No output"))
        st.subheader("Fraud Detection Agent Output")
        st.code(results.get("FraudDetectionAgent Output", "No output"))
        st.subheader("Claim Evaluation")
        st.code(results.get("ClaimEvaluatorAgent Output", "No output"))
        
        # download button
        parsed_claim = results.get("Parsed Claim", {})
        st.download_button(
            label="Download Claim (JSON)",
            data=json.dumps(parsed_claim, indent=2),
            file_name="parsed_claim.json",
            mime="application/json"
        )
        
        #  expander with detailed logs 
        with st.expander("Show detailed logs"):
            st.json(results)
