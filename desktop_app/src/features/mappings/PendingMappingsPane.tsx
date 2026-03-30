import { Button, Card, Space, Typography } from "antd";
import { PAGE_TEST_IDS } from "../../testing/selectors";
import {
  describePendingMappingItem,
  pendingRecordCompany,
  pendingSummary,
  type PendingMapping,
} from "./model";

type PendingMappingsPaneProps = {
  pendingPayload: Record<string, any>;
  pending: PendingMapping[];
  disabled: boolean;
  onImportAll: () => void;
  onImportItem: (item: PendingMapping) => void;
  onTriggerReprocess: () => void;
};

export function PendingMappingsPane({
  pendingPayload,
  pending,
  disabled,
  onImportAll,
  onImportItem,
  onTriggerReprocess,
}: PendingMappingsPaneProps) {
  return (
    <Card title="待补映射队列" data-testid={PAGE_TEST_IDS.mappings.pendingList} data-layout-region="queue">
      <Space direction="vertical" style={{ width: "100%" }}>
        <Typography.Text>{pendingSummary(pendingPayload, pending.length)}</Typography.Text>
        <Space wrap>
          <Button onClick={onImportAll}>导入全部待补项</Button>
          <Button id="runPendingMappingRefreshBtn" onClick={onTriggerReprocess} disabled={disabled}>
            一键重处理当前所有待补项
          </Button>
        </Space>
        {pending.length === 0 ? (
          <Typography.Text type="secondary">当前没有待补映射</Typography.Text>
        ) : (
          pending.map((item) => {
            const record = item.payload || {};
            const description = describePendingMappingItem(item);
            return (
              <Card key={item.record_id} size="small">
                <Space direction="vertical" style={{ width: "100%" }}>
                  <Typography.Text strong>{description.projectName}</Typography.Text>
                  <Typography.Text type="secondary">{description.projectCode}</Typography.Text>
                  <Typography.Text type="secondary">{`公司：${pendingRecordCompany(record) || "未识别"}`}</Typography.Text>
                  <Space>
                    <Button id="importPendingMappingBtn" onClick={() => onImportItem(item)}>导入到编辑器</Button>
                  </Space>
                </Space>
              </Card>
            );
          })
        )}
      </Space>
    </Card>
  );
}
