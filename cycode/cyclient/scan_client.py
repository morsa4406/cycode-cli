import json
from typing import TYPE_CHECKING, List, Optional, Set, Union

from requests import Response

from cycode.cli import consts
from cycode.cli.config import configuration_manager
from cycode.cli.exceptions.custom_exceptions import CycodeError
from cycode.cli.files_collector.models.in_memory_zip import InMemoryZip
from cycode.cyclient import models
from cycode.cyclient.cycode_client_base import CycodeClientBase

if TYPE_CHECKING:
    from cycode.cyclient.scan_config_base import ScanConfigBase


class ScanClient:
    def __init__(
        self, scan_cycode_client: CycodeClientBase, scan_config: 'ScanConfigBase', hide_response_log: bool = True
    ) -> None:
        self.scan_cycode_client = scan_cycode_client
        self.scan_config = scan_config

        self._SCAN_SERVICE_CONTROLLER_PATH = 'api/v1/scan'
        self._SCAN_SERVICE_CLI_CONTROLLER_PATH = 'api/v1/cli-scan'

        self._DETECTIONS_SERVICE_CONTROLLER_PATH = 'api/v1/detections'
        self._DETECTIONS_SERVICE_CLI_CONTROLLER_PATH = 'api/v1/detections/cli'

        self._POLICIES_SERVICE_CONTROLLER_PATH_V3 = 'api/v3/policies'

        self._hide_response_log = hide_response_log

    def get_scan_controller_path(self, scan_type: str, should_use_scan_service: bool = False) -> str:
        if not should_use_scan_service and scan_type == consts.INFRA_CONFIGURATION_SCAN_TYPE:
            # we don't use async flow for IaC scan yet
            return self._SCAN_SERVICE_CONTROLLER_PATH
        if not should_use_scan_service and scan_type == consts.SECRET_SCAN_TYPE:
            # if a secret scan goes to detector directly, we should not use CLI controller.
            # CLI controller belongs to the scan service only
            return self._SCAN_SERVICE_CONTROLLER_PATH

        return self._SCAN_SERVICE_CLI_CONTROLLER_PATH

    def get_detections_service_controller_path(self, scan_type: str) -> str:
        if scan_type == consts.INFRA_CONFIGURATION_SCAN_TYPE:
            # we don't use async flow for IaC scan yet
            return self._DETECTIONS_SERVICE_CONTROLLER_PATH

        return self._DETECTIONS_SERVICE_CLI_CONTROLLER_PATH

    @staticmethod
    def get_scan_flow_type(should_use_sync_flow: bool = False) -> str:
        if should_use_sync_flow:
            return '/sync'

        return ''

    def get_scan_service_url_path(
        self, scan_type: str, should_use_scan_service: bool = False, should_use_sync_flow: bool = False
    ) -> str:
        service_path = self.scan_config.get_service_name(scan_type, should_use_scan_service)
        controller_path = self.get_scan_controller_path(scan_type, should_use_scan_service)
        flow_type = self.get_scan_flow_type(should_use_sync_flow)
        return f'{service_path}/{controller_path}{flow_type}'

    def content_scan(self, scan_type: str, file_name: str, content: str, is_git_diff: bool = True) -> models.ScanResult:
        path = f'{self.get_scan_service_url_path(scan_type)}/content'
        body = {'name': file_name, 'content': content, 'is_git_diff': is_git_diff}
        response = self.scan_cycode_client.post(
            url_path=path, body=body, hide_response_content_log=self._hide_response_log
        )
        return self.parse_scan_response(response)

    def get_zipped_file_scan_url_path(self, scan_type: str) -> str:
        return f'{self.get_scan_service_url_path(scan_type)}/zipped-file'

    def zipped_file_scan(
        self, scan_type: str, zip_file: InMemoryZip, scan_id: str, scan_parameters: dict, is_git_diff: bool = False
    ) -> models.ZippedFileScanResult:
        files = {'file': ('multiple_files_scan.zip', zip_file.read())}

        response = self.scan_cycode_client.post(
            url_path=self.get_zipped_file_scan_url_path(scan_type),
            data={'scan_id': scan_id, 'is_git_diff': is_git_diff, 'scan_parameters': json.dumps(scan_parameters)},
            files=files,
            hide_response_content_log=self._hide_response_log,
        )

        return self.parse_zipped_file_scan_response(response)

    def get_scan_report_url(self, scan_id: str, scan_type: str) -> models.ScanReportUrlResponse:
        response = self.scan_cycode_client.get(url_path=self.get_scan_report_url_path(scan_id, scan_type))
        return models.ScanReportUrlResponseSchema().build_dto(response.json())

    def get_scan_aggregation_report_url(self, aggregation_id: str, scan_type: str) -> models.ScanReportUrlResponse:
        response = self.scan_cycode_client.get(
            url_path=self.get_scan_aggregation_report_url_path(aggregation_id, scan_type)
        )
        return models.ScanReportUrlResponseSchema().build_dto(response.json())

    def get_zipped_file_scan_async_url_path(self, scan_type: str, should_use_sync_flow: bool = False) -> str:
        async_scan_type = self.scan_config.get_async_scan_type(scan_type)
        async_entity_type = self.scan_config.get_async_entity_type(scan_type)
        scan_service_url_path = self.get_scan_service_url_path(
            scan_type, should_use_scan_service=True, should_use_sync_flow=should_use_sync_flow
        )
        return f'{scan_service_url_path}/{async_scan_type}/{async_entity_type}'

    def get_zipped_file_scan_sync_url_path(self, scan_type: str) -> str:
        server_scan_type = self.scan_config.get_async_scan_type(scan_type)
        scan_service_url_path = self.get_scan_service_url_path(
            scan_type, should_use_scan_service=True, should_use_sync_flow=True
        )
        return f'{scan_service_url_path}/{server_scan_type}/repository'

    def zipped_file_scan_sync(
        self,
        zip_file: InMemoryZip,
        scan_type: str,
        scan_parameters: dict,
        is_git_diff: bool = False,
    ) -> models.ScanResultsSyncFlow:
        files = {'file': ('multiple_files_scan.zip', zip_file.read())}

        if 'report' in scan_parameters:
            del scan_parameters['report']  # BE raises validation error instead of ignoring it

        response = self.scan_cycode_client.post(
            url_path=self.get_zipped_file_scan_sync_url_path(scan_type),
            data={
                'is_git_diff': is_git_diff,
                'scan_parameters': json.dumps(scan_parameters),
            },
            files=files,
            hide_response_content_log=self._hide_response_log,
            timeout=configuration_manager.get_sync_scan_timeout_in_seconds(),
        )
        return models.ScanResultsSyncFlowSchema().load(response.json())

    def zipped_file_scan_async(
        self,
        zip_file: InMemoryZip,
        scan_type: str,
        scan_parameters: dict,
        is_git_diff: bool = False,
        is_commit_range: bool = False,
    ) -> models.ScanInitializationResponse:
        files = {'file': ('multiple_files_scan.zip', zip_file.read())}
        response = self.scan_cycode_client.post(
            url_path=self.get_zipped_file_scan_async_url_path(scan_type),
            data={
                'is_git_diff': is_git_diff,
                'scan_parameters': json.dumps(scan_parameters),
                'is_commit_range': is_commit_range,
            },
            files=files,
        )
        return models.ScanInitializationResponseSchema().load(response.json())

    def multiple_zipped_file_scan_async(
        self,
        from_commit_zip_file: InMemoryZip,
        to_commit_zip_file: InMemoryZip,
        scan_type: str,
        scan_parameters: dict,
        is_git_diff: bool = False,
    ) -> models.ScanInitializationResponse:
        url_path = f'{self.get_scan_service_url_path(scan_type)}/{scan_type}/repository/commit-range'
        files = {
            'file_from_commit': ('multiple_files_scan.zip', from_commit_zip_file.read()),
            'file_to_commit': ('multiple_files_scan.zip', to_commit_zip_file.read()),
        }
        response = self.scan_cycode_client.post(
            url_path=url_path,
            data={'is_git_diff': is_git_diff, 'scan_parameters': json.dumps(scan_parameters)},
            files=files,
        )
        return models.ScanInitializationResponseSchema().load(response.json())

    def get_scan_details_path(self, scan_type: str, scan_id: str) -> str:
        return f'{self.get_scan_service_url_path(scan_type, should_use_scan_service=True)}/{scan_id}'

    def get_scan_report_url_path(self, scan_id: str, scan_type: str) -> str:
        return f'{self.get_scan_service_url_path(scan_type, should_use_scan_service=True)}/reportUrl/{scan_id}'

    def get_scan_aggregation_report_url_path(self, aggregation_id: str, scan_type: str) -> str:
        return (
            f'{self.get_scan_service_url_path(scan_type, should_use_scan_service=True)}'
            f'/reportUrlByAggregationId/{aggregation_id}'
        )

    def get_scan_details(self, scan_type: str, scan_id: str) -> models.ScanDetailsResponse:
        path = self.get_scan_details_path(scan_type, scan_id)
        response = self.scan_cycode_client.get(url_path=path)
        return models.ScanDetailsResponseSchema().load(response.json())

    def get_detection_rules_path(self) -> str:
        return (
            f'{self.scan_config.get_detections_prefix()}/'
            f'{self._POLICIES_SERVICE_CONTROLLER_PATH_V3}/'
            f'detection_rules/byIds'
        )

    def get_supported_modules_preferences(self) -> models.SupportedModulesPreferences:
        response = self.scan_cycode_client.get(url_path='preferences/api/v1/supportedmodules')
        return models.SupportedModulesPreferencesSchema().load(response.json())

    @staticmethod
    def get_ai_remediation_path(detection_id: str) -> str:
        return f'scm-remediator/api/v1/ContentRemediation/preview/{detection_id}'

    def get_ai_remediation(self, detection_id: str, *, fix: bool = False) -> str:
        path = self.get_ai_remediation_path(detection_id)

        data = {
            'resolving_parameters': {
                'get_diff': True,
                'use_code_snippet': True,
                'add_diff_header': True,
            }
        }
        if not fix:
            data['resolving_parameters']['remediation_action'] = 'ReplyWithRemediationDetails'

        response = self.scan_cycode_client.get(
            url_path=path, json=data, timeout=configuration_manager.get_ai_remediation_timeout_in_seconds()
        )
        return response.text.strip()

    @staticmethod
    def _get_policy_type_by_scan_type(scan_type: str) -> str:
        scan_type_to_policy_type = {
            consts.INFRA_CONFIGURATION_SCAN_TYPE: 'IaC',
            consts.SCA_SCAN_TYPE: 'SCA',
            consts.SECRET_SCAN_TYPE: 'SecretDetection',
            consts.SAST_SCAN_TYPE: 'SAST',
        }

        if scan_type not in scan_type_to_policy_type:
            raise CycodeError('Invalid scan type')

        return scan_type_to_policy_type[scan_type]

    @staticmethod
    def parse_detection_rules_response(response: Response) -> List[models.DetectionRule]:
        return models.DetectionRuleSchema().load(response.json(), many=True)

    def get_detection_rules(self, detection_rules_ids: Union[Set[str], List[str]]) -> List[models.DetectionRule]:
        response = self.scan_cycode_client.get(
            url_path=self.get_detection_rules_path(),
            params={'ids': detection_rules_ids},
            hide_response_content_log=self._hide_response_log,
        )

        return self.parse_detection_rules_response(response)

    def get_scan_detections_path(self, scan_type: str) -> str:
        return f'{self.scan_config.get_detections_prefix()}/{self.get_detections_service_controller_path(scan_type)}'

    @staticmethod
    def get_scan_detections_list_path_suffix(scan_type: str) -> str:
        # we don't use async flow for IaC scan yet
        if scan_type == consts.INFRA_CONFIGURATION_SCAN_TYPE:
            return ''

        return '/detections'

    def get_scan_detections_list_path(self, scan_type: str) -> str:
        return f'{self.get_scan_detections_path(scan_type)}{self.get_scan_detections_list_path_suffix(scan_type)}'

    def get_scan_raw_detections(self, scan_type: str, scan_id: str) -> List[dict]:
        params = {'scan_id': scan_id}

        page_size = 200

        raw_detections = []

        page_number = 0
        last_response_size = 0
        while page_number == 0 or last_response_size == page_size:
            params['page_size'] = page_size
            params['page_number'] = page_number

            response = self.scan_cycode_client.get(
                url_path=self.get_scan_detections_list_path(scan_type),
                params=params,
                hide_response_content_log=self._hide_response_log,
            ).json()
            raw_detections.extend(response)

            page_number += 1
            last_response_size = len(response)

        return raw_detections

    def commit_range_zipped_file_scan(
        self, scan_type: str, zip_file: InMemoryZip, scan_id: str
    ) -> models.ZippedFileScanResult:
        url_path = f'{self.get_scan_service_url_path(scan_type)}/commit-range-zipped-file'
        files = {'file': ('multiple_files_scan.zip', zip_file.read())}
        response = self.scan_cycode_client.post(
            url_path=url_path, data={'scan_id': scan_id}, files=files, hide_response_content_log=self._hide_response_log
        )
        return self.parse_zipped_file_scan_response(response)

    def get_report_scan_status_path(self, scan_type: str, scan_id: str, should_use_scan_service: bool = False) -> str:
        return f'{self.get_scan_service_url_path(scan_type, should_use_scan_service)}/{scan_id}/status'

    def report_scan_status(
        self, scan_type: str, scan_id: str, scan_status: dict, should_use_scan_service: bool = False
    ) -> None:
        self.scan_cycode_client.post(
            url_path=self.get_report_scan_status_path(
                scan_type, scan_id, should_use_scan_service=should_use_scan_service
            ),
            body=scan_status,
        )

    @staticmethod
    def parse_scan_response(response: Response) -> models.ScanResult:
        return models.ScanResultSchema().load(response.json())

    @staticmethod
    def parse_zipped_file_scan_response(response: Response) -> models.ZippedFileScanResult:
        return models.ZippedFileScanResultSchema().load(response.json())

    @staticmethod
    def get_service_name(scan_type: str) -> Optional[str]:
        # TODO(MarshalX): get_service_name should be removed from ScanClient? Because it exists in ScanConfig
        if scan_type == consts.SECRET_SCAN_TYPE:
            return 'secret'
        if scan_type == consts.INFRA_CONFIGURATION_SCAN_TYPE:
            return 'iac'
        if scan_type == consts.SCA_SCAN_TYPE or scan_type == consts.SAST_SCAN_TYPE:
            return 'scans'

        return None
