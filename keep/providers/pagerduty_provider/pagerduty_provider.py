import dataclasses
import datetime
import json
import typing
import uuid

import pydantic
import requests

from keep.api.models.alert import AlertDto, AlertSeverity, AlertStatus
from keep.contextmanager.contextmanager import ContextManager
from keep.exceptions.provider_config_exception import ProviderConfigException
from keep.providers.base.base_provider import BaseProvider
from keep.providers.models.provider_config import ProviderConfig, ProviderScope
from keep.providers.providers_factory import ProvidersFactory

# Todo: think about splitting in to PagerdutyIncidentsProvider and PagerdutyAlertsProvider
# Read this: https://community.pagerduty.com/forum/t/create-incident-using-python/3596/3


@pydantic.dataclasses.dataclass
class PagerdutyProviderAuthConfig:
    routing_key: str | None = dataclasses.field(
        metadata={
            "required": False,
            "description": "Routing Key (an integration or ruleset key)",
        },
        default=None,
    )
    api_key: str | None = dataclasses.field(
        metadata={
            "required": False,
            "description": "Api Key (a user or team API key)",
            "sensitive": True,
        },
        default=None,
    )


class PagerdutyProvider(BaseProvider):
    """Pull alerts and query incidents from PagerDuty."""

    PROVIDER_SCOPES = [
        ProviderScope(
            name="incidents_read",
            description="Read incidents data.",
            mandatory=True,
            alias="Incidents Data Read",
        ),
        ProviderScope(
            name="incidents_write",
            description="Write incidents.",
            mandatory=False,
            alias="Incidents Write",
        ),
        ProviderScope(
            name="webhook_subscriptions_read",
            description="Read webhook data.",
            mandatory=False,
            mandatory_for_webhook=True,
            alias="Webhooks Data Read",
        ),
        ProviderScope(
            name="webhook_subscriptions_write",
            description="Write webhooks.",
            mandatory=False,
            mandatory_for_webhook=True,
            alias="Webhooks Write",
        ),
    ]
    SUBSCRIPTION_API_URL = "https://api.pagerduty.com/webhook_subscriptions"
    PROVIDER_DISPLAY_NAME = "PagerDuty"
    SEVERITIES_MAP = {
        "P1": AlertSeverity.CRITICAL,
        "P2": AlertSeverity.HIGH,
        "P3": AlertSeverity.WARNING,
        "P4": AlertSeverity.INFO,
    }
    STATUS_MAP = {
        "triggered": AlertStatus.FIRING,
        "acknowledged": AlertStatus.ACKNOWLEDGED,
        "resolved": AlertStatus.RESOLVED,
    }
    DEFAULT_LOOKBACK_DAYS = 30  # Fetch incidents from the last 30 days by default
    MAX_INCIDENTS = 1000  # Maximum number of incidents to fetch
    INCLUDE_DETAILS = [
        "acknowledgers", "assignees", "conference_bridge", "escalation_policies",
        "first_trigger_log_entries", "priorities", "services", "teams", "users"
    ]

    def __init__(
        self, context_manager: ContextManager, provider_id: str, config: ProviderConfig
    ):
        super().__init__(context_manager, provider_id, config)

    def validate_config(self):
        self.authentication_config = PagerdutyProviderAuthConfig(
            **self.config.authentication
        )
        if (
            not self.authentication_config.routing_key
            and not self.authentication_config.api_key
        ):
            raise ProviderConfigException(
                "PagerdutyProvider requires either routing_key or api_key",
                provider_id=self.provider_id,
            )
        
    def validate_scopes(self):
        """
        Validate that the provider has the required scopes.
        """
        headers = {
            "Accept": "application/json",
            "Authorization": f"Token token={self.authentication_config.api_key}",
        }
        scopes = {}
        for scope in self.PROVIDER_SCOPES:
            try:
                # Todo: how to check validity for write scopes?
                if scope.name.startswith("incidents"):
                    response = requests.get(
                        "https://api.pagerduty.com/incidents",
                        headers=headers,
                    )
                elif scope.name.startswith("webhook_subscriptions"):
                    response = requests.get(
                        self.SUBSCRIPTION_API_URL,
                        headers=headers,
                    )
                if response.ok:
                    scopes[scope.name] = True
                else:
                    scopes[scope.name] = response.reason
            except Exception as e:
                self.logger.exception("Error validating scopes")
                scopes[scope.name] = str(e)
        return scopes

    def _build_alert(
        self, title: str, alert_body: str, dedup: str
    ) -> typing.Dict[str, typing.Any]:
        """
        Builds the payload for an event alert.

        Args:
            title: Title of alert
            alert_body: UTF-8 string of custom message for alert. Shown in incident body
            dedup: Any string, max 255, characters used to deduplicate alerts

        Returns:
            Dictionary of alert body for JSON serialization
        """
        return {
            "routing_key": self.authentication_config.routing_key,
            "event_action": "trigger",
            "dedup_key": dedup,
            "payload": {
                "summary": title,
                "source": "custom_event",
                "severity": "critical",
                "custom_details": {
                    "alert_body": alert_body,
                },
            },
        }

    def _send_alert(self, title: str, body: str, dedup: str | None = None):
        """
        Sends PagerDuty Alert

        Args:
            title: Title of the alert.
            alert_body: UTF-8 string of custom message for alert. Shown in incident body
            dedup: Any string, max 255, characters used to deduplicate alerts
        """
        # If no dedup is given, use epoch timestamp
        if dedup is None:
            dedup = str(datetime.datetime.now().timestamp())

        url = "https://events.pagerduty.com//v2/enqueue"

        result = requests.post(url, json=self._build_alert(title, body, dedup))

        self.logger.debug("Alert status: %s", result.status_code)
        self.logger.debug("Alert response: %s", result.text)
        return result.text

    def _trigger_incident(
        self,
        service_id: str,
        title: str,
        body: dict,
        requester: str,
        incident_key: str | None = None,
    ):
        """Triggers an incident via the V2 REST API using sample data."""

        if not incident_key:
            incident_key = str(uuid.uuid4()).replace("-", "")

        url = "https://api.pagerduty.com/incidents"
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/vnd.pagerduty+json;version=2",
            "Authorization": f"Token token={self.authentication_config.api_key}",
            "From": requester,
        }

        payload = {
            "incident": {
                "type": "incident",
                "title": title,
                "service": {"id": service_id, "type": "service_reference"},
                "incident_key": incident_key,
                "body": body,
            }
        }

        r = requests.post(url, headers=headers, data=json.dumps(payload))

        print(f"Status Code: {r.status_code}")
        print(r.json())
        return r.json()

    def dispose(self):
        """
        No need to dispose of anything, so just do nothing.
        """
        pass

    def setup_webhook(
        self, tenant_id: str, keep_api_url: str, api_key: str, setup_alerts: bool = True
    ):
        self.logger.info("Setting up Pagerduty webhook")
        headers = {"Authorization": f"Token token={self.authentication_config.api_key}"}
        request = requests.get(self.SUBSCRIPTION_API_URL, headers=headers)
        if not request.ok:
            raise Exception("Could not get existing webhooks")
        existing_webhooks = request.json().get("webhook_subscriptions", [])
        webhook_exists = next(
            iter(
                [
                    webhook
                    for webhook in existing_webhooks
                    if tenant_id in webhook.get("description")
                ]
            ),
            False,
        )
        webhook_payload = {
            "webhook_subscription": {
                "type": "webhook_subscription",
                "delivery_method": {
                    "type": "http_delivery_method",
                    "url": keep_api_url,
                    "custom_headers": [{"name": "X-API-KEY", "value": api_key}],
                },
                "description": f"Keep Pagerduty webhook ({tenant_id}) - do not change",
                "events": [
                    "incident.acknowledged",
                    "incident.annotated",
                    "incident.delegated",
                    "incident.escalated",
                    "incident.priority_updated",
                    "incident.reassigned",
                    "incident.reopened",
                    "incident.resolved",
                    "incident.responder.added",
                    "incident.responder.replied",
                    "incident.triggered",
                    "incident.unacknowledged",
                ],
                "filter": {"type": "account_reference"},
            },
        }
        if webhook_exists:
            self.logger.info("Webhook already exists, removing and re-creating")
            webhook_id = webhook_exists.get("id")
            request = requests.delete(
                f"{self.SUBSCRIPTION_API_URL}/{webhook_id}", headers=headers
            )
            if not request.ok:
                raise Exception("Could not remove existing webhook")
            self.logger.info("Webhook removed", extra={"webhook_id": webhook_id})

        self.logger.info("Creating Pagerduty webhook")
        request = requests.post(
            self.SUBSCRIPTION_API_URL,
            headers=headers,
            json=webhook_payload,
        )
        if not request.ok:
            self.logger.error("Failed to add webhook", extra=request.json())
            raise Exception("Could not create webhook")
        self.logger.info("Webhook created")

    def _get_alerts(self) -> list[AlertDto]:
            params = {
                "limit": 100,  # Number of incidents per page
                "sort_by": "created_at:desc",
                "since": (datetime.datetime.now() - datetime.timedelta(days=self.DEFAULT_LOOKBACK_DAYS)).isoformat(),
                "until": datetime.datetime.now().isoformat(),
                "include[]": self.INCLUDE_DETAILS
            }

            incidents = []
            while len(incidents) < self.MAX_INCIDENTS:
                request = requests.get(
                    "https://api.pagerduty.com/incidents",
                    headers={
                        "Authorization": f"Token token={self.authentication_config.api_key}",
                    },
                    params=params
                )
                if not request.ok:
                    self.logger.error("Failed to get alerts", extra=request.json())
                    raise Exception("Could not get alerts")
                
                response = request.json()
                new_incidents = response.get("incidents", [])
                incidents.extend(new_incidents)
                
                if not response.get("more") or len(new_incidents) == 0:
                    break
                
                params["offset"] = response.get("offset") + len(new_incidents)

            self.logger.info(f"Fetched {len(incidents)} incidents from PagerDuty")
            return [self._format_alert({"event": {"data": incident}}) for incident in incidents]

    @staticmethod
    def _format_alert(
        event: dict, provider_instance: typing.Optional["PagerdutyProvider"] = None
    ) -> AlertDto:
        actual_event = event.get("event", {})
        data = actual_event.get("data", {})
        url = data.pop("self", data.pop("html_url"))
        status = PagerdutyProvider.STATUS_MAP.get(data.pop("status"), AlertStatus.FIRING)
        priority = PagerdutyProvider.SEVERITIES_MAP.get(
            data.get("priority", {}).get("summary"), AlertSeverity.INFO
        )
        last_received = data.pop("created_at")
        name = data.pop("title")
        service = data.pop("service", {})
        service_name = service.get("summary", "unknown")
        environment = next(
            iter(
                [x for x in data.pop("custom_fields", []) if x.get("name") == "environment"]
            ),
            {},
        ).get("value", "unknown")

        # Extract additional metadata
        incident_number = data.get("incident_number")
        urgency = data.get("urgency")
        escalation_policy = data.get("escalation_policy", {}).get("summary")
        teams = [team.get("summary") for team in data.get("teams", [])]
        assignments = [
            {
                "assignee": assignment.get("assignee", {}).get("summary"),
                "at": assignment.get("at")
            }
            for assignment in data.get("assignments", [])
        ]
        description = data.get("description")
        
        # New fields from additional details
        acknowledgers = [ack.get("summary") for ack in data.get("acknowledgers", [])]
        first_trigger_log_entry = data.get("first_trigger_log_entry", {}).get("summary")
        conference_bridge = data.get("conference_bridge", {}).get("summary")

        return AlertDto(
            **data,
            url=url,
            status=status,
            lastReceived=last_received,
            name=name,
            severity=priority,
            environment=environment,
            source=["pagerduty"],
            service=service_name,
            incident_number=incident_number,
            urgency=urgency,
            escalation_policy=escalation_policy,
            teams=teams,
            assignments=assignments,
            description=description,
            impacted_services=[service_name],
            acknowledgers=acknowledgers,
            first_trigger_log_entry=first_trigger_log_entry,
            conference_bridge=conference_bridge
        )
    def _notify(
        self,
        title: str = "",
        alert_body: str = "",
        dedup: str = "",
        service_id: str = "",
        requester: str = "",
        incident_id: str = "",
        **kwargs: dict,
    ):
        """
        Create a PagerDuty alert.
            Alert/Incident is created either via the Events API or the Incidents API.
            See https://community.pagerduty.com/forum/t/create-incident-using-python/3596/3 for more information

        Args:
            kwargs (dict): The providers with context
        """
        if self.authentication_config.routing_key:
            return self._send_alert(title, alert_body, dedup=dedup, **kwargs)
        else:
            return self._trigger_incident(
                service_id, title, alert_body, requester, incident_id, **kwargs
            )


if __name__ == "__main__":
    # Output debug messages
    import logging

    logging.basicConfig(level=logging.DEBUG, handlers=[logging.StreamHandler()])
    context_manager = ContextManager(
        tenant_id="singletenant",
        workflow_id="test",
    )
    # Load environment variables
    import os

    api_key = os.environ.get("PAGERDUTY_API_KEY")

    provider_config = {
        "authentication": {"api_key": api_key},
    }
    provider = ProvidersFactory.get_provider(
        context_manager=context_manager,
        provider_id="keep-pd",
        provider_type="pagerduty",
        provider_config=provider_config,
    )
    results = provider.setup_webhook(
        "keep",
        "https://eb8a-77-137-44-66.ngrok-free.app/alerts/event/pagerduty?provider_id=keep-pd",
        "just-a-test",
        True,
    )
