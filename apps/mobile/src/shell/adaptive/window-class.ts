import { useWindowDimensions } from "react-native";
import {
  windowHeightClass,
  windowWidthClass,
  type WindowHeightClass,
  type WindowWidthClass,
} from "@next-tutor/design-tokens";

/**
 * 自适应窗口信息：只依据当前窗口尺寸，不看设备型号。
 * useWindowDimensions 在旋转/折叠/分屏时实时更新，严禁缓存为模块常量。
 */
export interface AdaptiveInfo {
  width: number;
  height: number;
  widthClass: WindowWidthClass;
  heightClass: WindowHeightClass;
  /** <600dp：底部导航 + 单 pane。 */
  isCompact: boolean;
  /** 600–839dp。 */
  isMedium: boolean;
  /** >=840dp。 */
  isExpandedUp: boolean;
  /** >=1200dp。 */
  isLargeUp: boolean;
  /** <480dp 高度：禁止多栏（横屏手机/折叠半屏）。 */
  compactHeight: boolean;
  /** 允许的最大内容 pane 数。 */
  maxPanes: 1 | 2 | 3;
}

/** Pane 策略纯函数：任何宽度 + CompactHeight 都强制单 pane。 */
export function resolveMaxPanes(
  widthClass: WindowWidthClass,
  heightClass: WindowHeightClass,
): 1 | 2 | 3 {
  if (heightClass === "compact") return 1;
  if (widthClass === "large" || widthClass === "extraLarge") return 3;
  if (widthClass === "expanded") return 2;
  return 1;
}

export function useAdaptive(): AdaptiveInfo {
  const { width, height } = useWindowDimensions();
  const widthClass = windowWidthClass(width);
  const heightClass = windowHeightClass(height);
  const compactHeight = heightClass === "compact";
  const isCompact = widthClass === "compact";
  const isMedium = widthClass === "medium";
  const isExpandedUp =
    widthClass === "expanded" ||
    widthClass === "large" ||
    widthClass === "extraLarge";
  const isLargeUp = widthClass === "large" || widthClass === "extraLarge";
  const maxPanes = resolveMaxPanes(widthClass, heightClass);

  return {
    width,
    height,
    widthClass,
    heightClass,
    isCompact,
    isMedium,
    isExpandedUp,
    isLargeUp,
    compactHeight,
    maxPanes,
  };
}
