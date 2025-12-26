import os
from datetime import datetime, timedelta, timezone

from datadog_api_client import ApiClient, Configuration
from datadog_api_client.v1.api.logs_api import LogsApi
from datadog_api_client.v1.model.logs_list_request import LogsListRequest
from datadog_api_client.v1.model.logs_list_request_time import LogsListRequestTime

host = os.getenv("DD_API_HOST")
api_key = os.getenv("DD_API_KEY")
app_key = os.getenv("DD_APP_KEY")

to_dt = datetime.now(timezone.utc)
from_dt = to_dt - timedelta(minutes=30)

configuration = Configuration()
configuration.host = host
configuration.api_key = {
    "apiKeyAuth": api_key,
    "appKeyAuth": app_key,
}

# configuration.verify_ssl = False
# configuration.verify_ssl_host = False
configuration.timeout = 30

body = LogsListRequest(
    query="kube_cluster_name:marios-stg-eks status:error",
    time=LogsListRequestTime(
        _from=from_dt,
        to=to_dt,
    ),
    limit=5,
)

with ApiClient(configuration) as api_client:
    api = LogsApi(api_client)
    resp = api.list_logs(body=body)

print("logs returned:", len(resp.logs or []))