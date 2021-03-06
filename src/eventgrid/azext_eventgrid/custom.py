# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for license information.
# --------------------------------------------------------------------------------------------

import re
from six.moves.urllib.parse import quote  # pylint: disable=import-error,relative-import
from knack.log import get_logger
from knack.util import CLIError
from msrestazure.tools import parse_resource_id
from dateutil.parser import parse   # pylint: disable=import-error,relative-import

from azure.cli.core.commands.client_factory import get_subscription_id
from azext_eventgrid.mgmt.eventgrid.models import (
    EventSubscription,
    EventSubscriptionUpdateParameters,
    WebHookEventSubscriptionDestination,
    Topic,
    Domain,
    JsonInputSchemaMapping,
    JsonField,
    JsonFieldWithDefault,
    RetryPolicy,
    EventHubEventSubscriptionDestination,
    StorageQueueEventSubscriptionDestination,
    HybridConnectionEventSubscriptionDestination,
    StorageBlobDeadLetterDestination,
    EventSubscriptionFilter)

logger = get_logger(__name__)

EVENTGRID_NAMESPACE = "Microsoft.EventGrid"
RESOURCES_NAMESPACE = "Microsoft.Resources"
SUBSCRIPTIONS = "subscriptions"
RESOURCE_GROUPS = "resourcegroups"
EVENTGRID_DOMAINS = "domains"
EVENTGRID_TOPICS = "topics"
WEBHOOK_DESTINATION = "webhook"
EVENTHUB_DESTINATION = "eventhub"
STORAGEQUEUE_DESTINATION = "storagequeue"
HYBRIDCONNECTION_DESTINATION = "hybridconnection"
EVENTGRID_SCHEMA = "EventGridSchema"
CLOUDEVENTV01_SCHEMA = "CloudEventV01Schema"
CUSTOM_EVENT_SCHEMA = "CustomEventSchema"
CUSTOM_INPUT_SCHEMA = "CustomInputSchema"
GLOBAL = "global"

# Deprecated event delivery schema value (starting 2018-09-15-preview)
INPUT_EVENT_SCHEMA = "InputEventSchema"

# Constants for the target field names of the mapping
TOPIC = "topic"
SUBJECT = "subject"
ID = "id"
EVENTTIME = "eventtime"
EVENTTYPE = "eventtype"
DATAVERSION = "dataversion"


def cli_topic_list(
        client,
        resource_group_name=None):
    if resource_group_name:
        return client.list_by_resource_group(resource_group_name)

    return client.list_by_subscription()


def cli_topic_create_or_update(
        client,
        resource_group_name,
        topic_name,
        location,
        tags=None,
        input_schema=EVENTGRID_SCHEMA,
        input_mapping_fields=None,
        input_mapping_default_values=None):
    final_input_schema, input_schema_mapping = _get_input_schema_and_mapping(
        input_schema,
        input_mapping_fields,
        input_mapping_default_values)
    topic_info = Topic(
        location=location,
        tags=tags,
        input_schema=final_input_schema,
        input_schema_mapping=input_schema_mapping)

    async_topic_create = client.create_or_update(
        resource_group_name,
        topic_name,
        topic_info)
    created_topic = async_topic_create.result()
    return created_topic


def cli_domain_list(
        client,
        resource_group_name=None):
    if resource_group_name:
        return client.list_by_resource_group(resource_group_name)

    return client.list_by_subscription()


def cli_domain_create_or_update(
        client,
        resource_group_name,
        domain_name,
        location,
        tags=None,
        input_schema=EVENTGRID_SCHEMA,
        input_mapping_fields=None,
        input_mapping_default_values=None):
    final_input_schema, input_schema_mapping = _get_input_schema_and_mapping(
        input_schema,
        input_mapping_fields,
        input_mapping_default_values)
    domain_info = Domain(
        location=location,
        tags=tags,
        input_schema=final_input_schema,
        input_schema_mapping=input_schema_mapping)

    async_domain_create = client.create_or_update(
        resource_group_name,
        domain_name,
        domain_info)
    created_domain = async_domain_create.result()
    return created_domain


def cli_eventgrid_event_subscription_create(   # pylint: disable=too-many-locals
        cmd,
        client,
        event_subscription_name,
        endpoint,
        resource_id=None,
        source_resource_id=None,
        resource_group_name=None,
        topic_name=None,
        endpoint_type=WEBHOOK_DESTINATION,
        included_event_types=None,
        subject_begins_with=None,
        subject_ends_with=None,
        is_subject_case_sensitive=False,
        max_delivery_attempts=30,
        event_ttl=1440,
        event_delivery_schema=None,
        deadletter_endpoint=None,
        labels=None,
        expiration_date=None,
        advanced_filter=None):
    scope = _get_scope_for_event_subscription(
        cli_ctx=cmd.cli_ctx,
        source_resource_id=source_resource_id,
        resource_id=resource_id,
        topic_name=topic_name,
        resource_group_name=resource_group_name)

    # Construct RetryPolicy based on max_delivery_attempts and event_ttl
    max_delivery_attempts = int(max_delivery_attempts)
    event_ttl = int(event_ttl)
    _validate_retry_policy(max_delivery_attempts, event_ttl)
    retry_policy = RetryPolicy(max_delivery_attempts=max_delivery_attempts, event_time_to_live_in_minutes=event_ttl)

    destination = _get_endpoint_destination(endpoint_type, endpoint)

    event_subscription_filter = EventSubscriptionFilter(
        subject_begins_with=subject_begins_with,
        subject_ends_with=subject_ends_with,
        included_event_types=included_event_types,
        is_subject_case_sensitive=is_subject_case_sensitive,
        advanced_filters=advanced_filter)

    deadletter_destination = None
    if deadletter_endpoint is not None:
        deadletter_destination = _get_deadletter_destination(deadletter_endpoint)

    if expiration_date is not None:
        expiration_date = parse(expiration_date)

    event_subscription_info = EventSubscription(
        destination=destination,
        filter=event_subscription_filter,
        labels=labels,
        event_delivery_schema=_get_event_delivery_schema(event_delivery_schema),
        retry_policy=retry_policy,
        expiration_time_utc=expiration_date,
        dead_letter_destination=deadletter_destination)

    _warn_if_manual_handshake_needed(endpoint_type, endpoint)

    return client.create_or_update(
        scope,
        event_subscription_name,
        event_subscription_info).result()


def cli_eventgrid_event_subscription_delete(
        cmd,
        client,
        event_subscription_name,
        resource_id=None,
        source_resource_id=None,
        resource_group_name=None,
        topic_name=None):
    scope = _get_scope_for_event_subscription(
        cli_ctx=cmd.cli_ctx,
        source_resource_id=source_resource_id,
        resource_id=resource_id,
        topic_name=topic_name,
        resource_group_name=resource_group_name)
    async_event_subscription_delete = client.delete(
        scope,
        event_subscription_name)
    return async_event_subscription_delete.result()


def event_subscription_setter(
        cmd,
        client,
        parameters,
        event_subscription_name,
        source_resource_id=None,
        resource_id=None,
        resource_group_name=None,
        topic_name=None):
    scope = _get_scope_for_event_subscription(
        cli_ctx=cmd.cli_ctx,
        source_resource_id=source_resource_id,
        resource_id=resource_id,
        topic_name=topic_name,
        resource_group_name=resource_group_name)

    async_event_subscription_update = client.update(
        scope,
        event_subscription_name,
        parameters)
    updated_event_subscription = async_event_subscription_update.result()
    return updated_event_subscription


def cli_eventgrid_event_subscription_get(
        cmd,
        client,
        event_subscription_name,
        source_resource_id=None,
        resource_id=None,
        resource_group_name=None,
        topic_name=None,
        include_full_endpoint_url=False):
    scope = _get_scope_for_event_subscription(
        cli_ctx=cmd.cli_ctx,
        source_resource_id=source_resource_id,
        resource_id=resource_id,
        topic_name=topic_name,
        resource_group_name=resource_group_name)

    retrieved_event_subscription = client.get(scope, event_subscription_name)
    destination = retrieved_event_subscription.destination
    if include_full_endpoint_url and isinstance(destination, WebHookEventSubscriptionDestination):
        full_endpoint_url = client.get_full_url(scope, event_subscription_name)
        destination.endpoint_url = full_endpoint_url.endpoint_url

    return retrieved_event_subscription


def cli_event_subscription_list(   # pylint: disable=too-many-return-statements
        client,
        resource_id=None,
        source_resource_id=None,
        topic_name=None,
        resource_group_name=None,
        location=None,
        topic_type_name=None):
    if source_resource_id is not None:
        # If Source Resource ID is specified, we need to list event subscriptions for that particular resource.
        # No other parameters must be specified
        if (resource_group_name is not None or location is not None or
                topic_type_name is not None or resource_id is not None):
            raise CLIError('usage error: Since --source-resource-id is specified, none of the other parameters must'
                           'be specified.')

        return _list_event_subscriptions_by_resource_id(client, source_resource_id)

    if resource_id is not None:
        # DEPRECATED
        # If resource ID is specified, we need to list event subscriptions for that particular resource.
        # No other parameters must be specified
        if resource_group_name is not None or location is not None or topic_type_name is not None:
            raise CLIError('usage error: Since --resource-id is specified, none of the other parameters must'
                           ' be specified.')

        return _list_event_subscriptions_by_resource_id(client, resource_id)

    if topic_name:
        # DEPRECATED
        if resource_group_name is None:
            raise CLIError('Since --topic-name is specified, --resource-group-name must also be specified.')

        return client.list_by_resource(
            resource_group_name,
            EVENTGRID_NAMESPACE,
            EVENTGRID_TOPICS,
            topic_name)

    if location is None:
        # Since resource-id was not specified, location must be specified: e.g. "westus2" or "global". If not error OUT.
        raise CLIError('usage error: --source-resource-id ID | --location LOCATION'
                       ' [--resource-group-name RG] [--topic-type-name TOPIC_TYPE_NAME]')

    if topic_type_name is None:
        # No topic-type is specified: return event subscriptions across all topic types for this location.
        if location.lower() == GLOBAL.lower():
            if resource_group_name:
                return client.list_global_by_resource_group(resource_group_name)
            return client.list_global_by_subscription()

        if resource_group_name:
            return client.list_regional_by_resource_group(resource_group_name, location)
        return client.list_regional_by_subscription(location)

    # Topic type name is specified
    if location.lower() == GLOBAL.lower():
        if not _is_topic_type_global_resource(topic_type_name):
            raise CLIError('Invalid usage: Global cannot be specified for the location '
                           'as the specified topic type is a regional topic type with '
                           'regional event subscriptions. Specify a location value such '
                           'as westus. Global can be used only for global topic types: '
                           'Microsoft.Resources.Subscriptions and Microsoft.Resources.ResourceGroups.')
        if resource_group_name:
            return client.list_global_by_resource_group_for_topic_type(resource_group_name, topic_type_name)
        return client.list_global_by_subscription_for_topic_type(topic_type_name)

    if resource_group_name:
        return client.list_regional_by_resource_group_for_topic_type(resource_group_name, location, topic_type_name)
    return client.list_regional_by_subscription_for_topic_type(location, topic_type_name)


def _get_scope(
        cli_ctx,
        resource_group_name,
        provider_namespace,
        resource_type,
        resource_name):
    subscription_id = get_subscription_id(cli_ctx)

    if provider_namespace == RESOURCES_NAMESPACE:
        if resource_group_name:
            scope = (
                '/subscriptions/{}/resourceGroups/{}'
                .format(quote(subscription_id),
                        quote(resource_group_name)))
        else:
            scope = (
                '/subscriptions/{}'
                .format(quote(subscription_id)))
    else:
        scope = (
            '/subscriptions/{}/resourceGroups/{}/providers/{}/{}/{}'
            .format(quote(subscription_id),
                    quote(resource_group_name),
                    quote(provider_namespace),
                    quote(resource_type),
                    quote(resource_name)))

    return scope


def _get_scope_for_event_subscription(
        cli_ctx,
        resource_id,
        source_resource_id,
        topic_name,
        resource_group_name):
    if all([resource_id, source_resource_id]):
        raise CLIError('usage error: specify either "--resource-id" or "--source-resource-id", not both.')

    if all([resource_id, topic_name]):
        raise CLIError('usage error: specify either "--topic-name" or "--resource-id", not both.')

    if all([resource_id, resource_group_name]):
        raise CLIError('usage error: specify either "--resource_group_name" or "--resource-id", not both.')

    if all([source_resource_id, topic_name]):
        raise CLIError('usage error: specify either "--topic-name" or "--source-resource-id", not both.')

    if all([source_resource_id, resource_group_name]):
        raise CLIError('usage error: specify either "--resource_group_name" or "--source-resource-id", not both.')

    if source_resource_id:
        # Source Resource ID is provided, use that as the scope for the event subscription.
        # This is the latest non-deprecated way of specifying the source resource.
        scope = source_resource_id
    elif resource_id:
        # Deprecated
        scope = resource_id
    elif topic_name:
        # DEPRECATED: Topic name is provided, use the topic and resource group to build a scope for the user topic
        if resource_group_name is None:
            raise CLIError("When --topic-name is specified, the --resource-group-name must also be specified.")

        scope = _get_scope(cli_ctx, resource_group_name, EVENTGRID_NAMESPACE, EVENTGRID_TOPICS, topic_name)
    elif resource_group_name:
        # DEPRECATED: Event subscription to a resource group.
        scope = _get_scope(cli_ctx, resource_group_name, RESOURCES_NAMESPACE, RESOURCE_GROUPS, resource_group_name)
    else:
        # DEPRECATED
        logger.warning('This default option uses Azure subscription as the source resource.'
                       ' This is deprecated and will be removed in a future release.'
                       ' Use `--source-resource-id /subscriptions/{subid}` instead.')
        scope = _get_scope(cli_ctx, None, RESOURCES_NAMESPACE, SUBSCRIPTIONS, get_subscription_id(cli_ctx))

    return scope


def event_subscription_getter(
        cmd,
        client,
        event_subscription_name,
        source_resource_id=None,
        resource_id=None,
        resource_group_name=None,
        topic_name=None):
    scope = _get_scope_for_event_subscription(
        cli_ctx=cmd.cli_ctx,
        source_resource_id=source_resource_id,
        resource_id=resource_id,
        topic_name=topic_name,
        resource_group_name=resource_group_name)
    return client.get(scope, event_subscription_name)


def get_input_schema_mapping(
        input_mapping_fields=None,
        input_mapping_default_values=None):
    input_schema_mapping = None

    if input_mapping_fields is not None or input_mapping_default_values is not None:
        input_schema_mapping = JsonInputSchemaMapping()

        input_schema_mapping.id = JsonField()
        input_schema_mapping.topic = JsonField()
        input_schema_mapping.event_time = JsonField()
        input_schema_mapping.subject = JsonFieldWithDefault()
        input_schema_mapping.event_type = JsonFieldWithDefault()
        input_schema_mapping.data_version = JsonFieldWithDefault()

        if input_mapping_fields is not None:
            for field_mapping_pair in input_mapping_fields:
                field_mapping = field_mapping_pair.split("=")
                target = field_mapping[0]
                source = field_mapping[1]

                if target.lower() == ID.lower():
                    input_schema_mapping.id.source_field = source
                elif target.lower() == EVENTTIME.lower():
                    input_schema_mapping.event_time.source_field = source
                elif target.lower() == TOPIC.lower():
                    input_schema_mapping.topic.source_field = source
                elif target.lower() == SUBJECT.lower():
                    input_schema_mapping.subject.source_field = source
                elif target.lower() == DATAVERSION.lower():
                    input_schema_mapping.data_version.source_field = source
                elif target.lower() == EVENTTYPE.lower():
                    input_schema_mapping.event_type.source_field = source

        if input_mapping_default_values is not None:
            for default_value_mapping_pair in input_mapping_default_values:
                default_value_mapping = default_value_mapping_pair.split("=")
                target = default_value_mapping[0]
                source = default_value_mapping[1]

                if target.lower() == SUBJECT.lower():
                    input_schema_mapping.subject.default_value = source
                elif target.lower() == DATAVERSION.lower():
                    input_schema_mapping.data_version.default_value = source
                elif target.lower() == EVENTTYPE.lower():
                    input_schema_mapping.event_type.default_value = source

    return input_schema_mapping


def update_event_subscription(
        instance,
        endpoint=None,
        endpoint_type=WEBHOOK_DESTINATION,
        subject_begins_with=None,
        subject_ends_with=None,
        included_event_types=None,
        labels=None,
        deadletter_endpoint=None):
    event_subscription_destination = None
    deadletter_destination = None
    event_subscription_labels = instance.labels
    event_subscription_filter = instance.filter

    # TODO: These are not currently updatable, make them updatable.
    event_delivery_schema = instance.event_delivery_schema
    retry_policy = instance.retry_policy

    if endpoint_type.lower() != WEBHOOK_DESTINATION.lower() and endpoint is None:
        raise CLIError('Invalid usage: Since --endpoint-type is specified, a valid endpoint must also be specified.')

    if endpoint is not None:
        event_subscription_destination = _get_endpoint_destination(endpoint_type, endpoint)

    if deadletter_endpoint is not None:
        deadletter_destination = _get_deadletter_destination(deadletter_endpoint)

    if subject_begins_with is not None:
        event_subscription_filter.subject_begins_with = subject_begins_with

    if subject_ends_with is not None:
        event_subscription_filter.subject_ends_with = subject_ends_with

    if included_event_types is not None:
        event_subscription_filter.included_event_types = included_event_types

    if labels is not None:
        event_subscription_labels = labels

    params = EventSubscriptionUpdateParameters(
        destination=event_subscription_destination,
        filter=event_subscription_filter,
        labels=event_subscription_labels,
        retry_policy=retry_policy,
        dead_letter_destination=deadletter_destination,
        event_delivery_schema=event_delivery_schema
    )

    return params


def _get_endpoint_destination(endpoint_type, endpoint):
    if endpoint_type.lower() == WEBHOOK_DESTINATION.lower():
        destination = WebHookEventSubscriptionDestination(endpoint_url=endpoint)
    elif endpoint_type.lower() == EVENTHUB_DESTINATION.lower():
        destination = EventHubEventSubscriptionDestination(resource_id=endpoint)
    elif endpoint_type.lower() == HYBRIDCONNECTION_DESTINATION.lower():
        destination = HybridConnectionEventSubscriptionDestination(resource_id=endpoint)
    elif endpoint_type.lower() == STORAGEQUEUE_DESTINATION.lower():
        destination = _get_storage_queue_destination(endpoint)

    return destination


def _get_storage_queue_destination(endpoint):
    # Supplied endpoint would be in the following format:
    # /subscriptions/.../storageAccounts/sa1/queueServices/default/queues/{queueName}))
    # and we need to break it up into:
    # /subscriptions/.../storageAccounts/sa1 and queueName
    queue_items = re.split(
        "/queueServices/default/queues/", endpoint, flags=re.IGNORECASE)

    if len(queue_items) != 2 or queue_items[0] is None or queue_items[1] is None:
        raise CLIError('Argument Error: Expected format of --endpoint for storage queue is:' +
                       '/subscriptions/id/resourceGroups/rg/providers/Microsoft.Storage/' +
                       'storageAccounts/sa1/queueServices/default/queues/queueName')

    destination = StorageQueueEventSubscriptionDestination(
        resource_id=queue_items[0], queue_name=queue_items[1])

    return destination


def _get_deadletter_destination(deadletter_endpoint):
    blob_items = re.split(
        "/blobServices/default/containers/", deadletter_endpoint, flags=re.IGNORECASE)

    if len(blob_items) != 2 or blob_items[0] is None or blob_items[1] is None:
        raise CLIError('Argument Error: Expected format of --deadletter-endpoint is:' +
                       '/subscriptions/id/resourceGroups/rg/providers/Microsoft.Storage/' +
                       'storageAccounts/sa1/blobServices/default/containers/containerName')

    deadletter_destination = StorageBlobDeadLetterDestination(
        resource_id=blob_items[0], blob_container_name=blob_items[1])

    return deadletter_destination


def _validate_retry_policy(max_delivery_attempts, event_ttl):
    if max_delivery_attempts < 1 or max_delivery_attempts > 30:
        raise CLIError('--max-delivery-attempts should be a number between 1 and 30.')

    if event_ttl < 1 or event_ttl > 1440:
        raise CLIError('--event-ttl should be a number between 1 and 1440.')


def _get_event_delivery_schema(event_delivery_schema):
    if event_delivery_schema is None:
        return None
    if event_delivery_schema.lower() == EVENTGRID_SCHEMA.lower():
        event_delivery_schema = EVENTGRID_SCHEMA
    elif event_delivery_schema.lower() == CUSTOM_INPUT_SCHEMA.lower():
        event_delivery_schema = CUSTOM_INPUT_SCHEMA
    elif event_delivery_schema.lower() == CLOUDEVENTV01_SCHEMA.lower():
        event_delivery_schema = CLOUDEVENTV01_SCHEMA
    # NO LONGER SUPPORTED
    elif event_delivery_schema.lower() == INPUT_EVENT_SCHEMA.lower():
        raise CLIError('usage error: InputEventSchema is not supported anymore. '
                       '--event-delivery-schema supported values are'
                       ':' + EVENTGRID_SCHEMA + ',' + CUSTOM_INPUT_SCHEMA +
                       ',' + CLOUDEVENTV01_SCHEMA)
    else:
        raise CLIError('usage error: --event-delivery-schema supported values are'
                       ' :' + EVENTGRID_SCHEMA + ',' + CUSTOM_INPUT_SCHEMA +
                       ',' + CLOUDEVENTV01_SCHEMA)

    return event_delivery_schema


def _warn_if_manual_handshake_needed(endpoint_type, endpoint):
    # If the endpoint belongs to a service that we know implements the subscription validation
    # handshake, there's no need to show this message, hence we check for those services
    # before showing this message. This list includes Azure Automation, EventGrid Trigger based
    # Azure functions, and Azure Logic Apps.
    if endpoint_type.lower() == WEBHOOK_DESTINATION.lower() and \
       "azure-automation" not in endpoint.lower() and \
       "eventgridextension" not in endpoint.lower() and \
       "logic.azure" not in endpoint.lower():
        logger.warning("If the provided endpoint doesn't support subscription validation " +
                       "handshake, navigate to the validation URL that you receive in the " +
                       "subscription validation event, in order to complete the event " +
                       "subscription creation or update. For more details, " +
                       "please visit http://aka.ms/esvalidation")


def _get_input_schema_and_mapping(
        input_schema=EVENTGRID_SCHEMA,
        input_mapping_fields=None,
        input_mapping_default_values=None):
    if input_schema.lower() == EVENTGRID_SCHEMA.lower():
        input_schema = EVENTGRID_SCHEMA
    elif input_schema.lower() == CUSTOM_EVENT_SCHEMA.lower():
        input_schema = CUSTOM_EVENT_SCHEMA
    elif input_schema.lower() == CLOUDEVENTV01_SCHEMA.lower():
        input_schema = CLOUDEVENTV01_SCHEMA
    else:
        raise CLIError('The provided --input-schema is not valid. The supported values are: ' +
                       EVENTGRID_SCHEMA + ',' + CUSTOM_EVENT_SCHEMA + ',' + CLOUDEVENTV01_SCHEMA)

    if input_schema in (EVENTGRID_SCHEMA, CLOUDEVENTV01_SCHEMA):
        # Ensure that custom input mappings are not specified
        if input_mapping_fields is not None or input_mapping_default_values is not None:
            raise CLIError('--input-mapping-default-values and --input-mapping-fields should be ' +
                           'specified only when --input-schema is set to customeventschema.')

    if input_schema == CUSTOM_EVENT_SCHEMA:
        # Ensure that custom input mappings are specified
        if input_mapping_fields is None and input_mapping_default_values is None:
            raise CLIError('Either --input-mapping-default-values or --input-mapping-fields must be ' +
                           'specified when --input-schema is set to customeventschema.')

    input_schema_mapping = get_input_schema_mapping(
        input_mapping_fields,
        input_mapping_default_values)

    return input_schema, input_schema_mapping


def _list_event_subscriptions_by_resource_id(client, resource_id):
    # parse_resource_id doesn't handle resource_ids for Azure subscriptions and RGs
    # so, first try to look for those two patterns.
    if resource_id is not None:
        id_parts = list(filter(None, resource_id.split('/')))
        if len(id_parts) < 5:
            # Azure subscriptions or Resource group
            if id_parts[0].lower() != "subscriptions":
                raise CLIError('The specified value for resource-id is not in the'
                               ' expected format. It should start with /subscriptions.')

            subscription_id = id_parts[1]
            _validate_subscription_id_matches_default_subscription_id(
                default_subscription_id=client.config.subscription_id,
                provided_subscription_id=subscription_id)

            if len(id_parts) == 2:
                return client.list_global_by_subscription_for_topic_type("Microsoft.Resources.Subscriptions")

            if len(id_parts) == 4 and id_parts[2].lower() == "resourcegroups":
                resource_group_name = id_parts[3]
                if resource_group_name is None:
                    raise CLIError('The specified value for resource-id is not'
                                   ' in the expected format. A valid value for'
                                   ' resource group must be provided.')
                return client.list_global_by_resource_group_for_topic_type(
                    resource_group_name,
                    "Microsoft.Resources.ResourceGroups")

    id_parts = parse_resource_id(resource_id)
    subscription_id = id_parts.get('subscription')
    _validate_subscription_id_matches_default_subscription_id(
        default_subscription_id=client.config.subscription_id,
        provided_subscription_id=subscription_id)

    rg_name = id_parts.get('resource_group')
    resource_name = id_parts.get('name')
    namespace = id_parts.get('namespace')
    resource_type = id_parts.get('type')

    if (subscription_id is None or rg_name is None or resource_name is None or
            namespace is None or resource_type is None):
        raise CLIError('The specified value for resource-id is not'
                       ' in the expected format.')

    # If this is for a domain topic, invoke the appropriate operation
    if (namespace.lower() == EVENTGRID_NAMESPACE.lower() and resource_type.lower() == EVENTGRID_DOMAINS.lower()):
        child_resource_type = id_parts.get('child_type_1')
        child_resource_name = id_parts.get('child_name_1')

        if (child_resource_type is not None and child_resource_type.lower() == EVENTGRID_TOPICS.lower() and
                child_resource_name is not None):
            return client.list_by_domain_topic(rg_name, resource_name, child_resource_name)

    # Not a domain topic, invoke the standard list_by_resource
    return client.list_by_resource(
        rg_name,
        namespace,
        resource_type,
        resource_name)


def _is_topic_type_global_resource(topic_type_name):
    # TODO: Add here if any other global topic types get added in the future.
    TOPIC_TYPE_AZURE_SUBSCRIPTIONS = "Microsoft.Resources.Subscriptions"
    TOPIC_TYPE_AZURE_RESOURCE_GROUP = "Microsoft.Resources.ResourceGroups"
    TOPIC_TYPE_MAPS_ACCOUNTS = "Microsoft.Maps.Accounts"

    if (topic_type_name.lower() == TOPIC_TYPE_AZURE_SUBSCRIPTIONS.lower() or
            topic_type_name.lower() == TOPIC_TYPE_MAPS_ACCOUNTS or
            topic_type_name.lower() == TOPIC_TYPE_AZURE_RESOURCE_GROUP.lower()):
        return True

    return False


def _validate_subscription_id_matches_default_subscription_id(
        default_subscription_id,
        provided_subscription_id):
    # The CLI/SDK infrastructure doesn't support overriding the subscription ID.
    # Hence, we validate that the provided subscription ID is the same as the default
    # configured subscription.
    if provided_subscription_id.lower() != default_subscription_id.lower():
        raise CLIError('The subscription ID in the specified resource-id'
                       ' does not match the default subscription ID. To set the default subscription ID,'
                       ' use az account set ID_OR_NAME, or use the global argument --subscription ')
