import type { ReactNode } from "react";
import { Layout, Menu, Typography } from "antd";
import type { MenuProps } from "antd";
import { SHELL_TEST_IDS } from "./testing/selectors";

const { Header, Sider, Content } = Layout;

const MENU_ITEMS: Array<{ key: string; label: string; testId: string }> = [
  { key: "overview", label: "总览", testId: SHELL_TEST_IDS.navOverview },
  { key: "tasks", label: "任务", testId: SHELL_TEST_IDS.navTasks },
  { key: "records", label: "记录", testId: SHELL_TEST_IDS.navRecords },
  { key: "mappings", label: "映射", testId: SHELL_TEST_IDS.navMappings },
  { key: "settings", label: "设置", testId: SHELL_TEST_IDS.navSettings },
];

type AppShellProps = {
  activeKey: string;
  onSelect: (key: string) => void;
  children: ReactNode;
};

export function AppShell({ activeKey, onSelect, children }: AppShellProps) {
  const items: MenuProps["items"] = MENU_ITEMS.map((item) => ({
    key: item.key,
    label: <span data-testid={item.testId}>{item.label}</span>,
  }));

  return (
    <Layout data-testid={SHELL_TEST_IDS.app} style={{ minHeight: "100vh" }}>
      <Sider width={240} theme="light">
        <div style={{ padding: "20px 16px 8px" }}>
          <Typography.Title level={4} style={{ marginBottom: 4 }}>
            产权交易所自动录入
          </Typography.Title>
          <Typography.Text type="secondary">Desktop control surface</Typography.Text>
        </div>
        <Menu
          mode="inline"
          selectedKeys={[activeKey]}
          items={items}
          onSelect={({ key }) => onSelect(String(key))}
        />
      </Sider>
      <Layout>
        <Header style={{ background: "#fff", padding: "16px 24px", height: "auto" }}>
          <Typography.Title level={3} style={{ margin: 0 }}>
            自动录入与导出控制台
          </Typography.Title>
        </Header>
        <Content data-testid={SHELL_TEST_IDS.content} style={{ padding: 24 }}>
          {children}
        </Content>
      </Layout>
    </Layout>
  );
}
