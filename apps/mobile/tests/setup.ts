jest.mock("expo-secure-store", () => {
  const table = new Map<string, string>();
  return {
    getItemAsync: jest.fn(async (key: string) => table.get(key) ?? null),
    setItemAsync: jest.fn(async (key: string, value: string) => {
      table.set(key, value);
    }),
    deleteItemAsync: jest.fn(async (key: string) => {
      table.delete(key);
    }),
    __table: table,
  };
});
jest.mock("@react-native-async-storage/async-storage", () => {
  const table = new Map<string, string>();
  return {
    getItem: jest.fn(async (key: string) => table.get(key) ?? null),
    setItem: jest.fn(async (key: string, value: string) => {
      table.set(key, value);
    }),
    removeItem: jest.fn(async (key: string) => {
      table.delete(key);
    }),
    getAllKeys: jest.fn(async () => [...table.keys()]),
    clear: jest.fn(async () => {
      table.clear();
    }),
    multiGet: jest.fn(async (keys: string[]) =>
      keys.map((key) => [key, table.get(key) ?? null]),
    ),
  };
});
jest.mock("expo-crypto", () => ({
  randomUUID: () => "12345678-1234-4567-890a-123456789abc",
}));
jest.mock("react-native-webview", () => {
  const React = require("react");
  const { View } = require("react-native");
  return {
    WebView: React.forwardRef((props: object, _ref: unknown) =>
      React.createElement(View, props),
    ),
  };
});
// Unit tests inspect native controls, while icon rendering is covered by device screenshots.
jest.mock(
  "lucide-react-native",
  () =>
    new Proxy(
      {},
      { get: (_target, key) => (key === "__esModule" ? false : () => null) },
    ),
);
export {};
