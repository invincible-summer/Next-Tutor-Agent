import React from "react";
import Svg, { Circle, Path, Rect, G, Line } from "react-native-svg";
import { useTheme } from "./ThemeProvider";
export function LearningArt({ compact = false }: { compact?: boolean }) {
  const { theme } = useTheme();
  const color = theme.colors.accent;
  return (
    <Svg
      width="100%"
      height={compact ? 148 : 272}
      viewBox="0 0 360 280"
      accessible={false}
    >
      <Circle cx="180" cy="140" r="116" fill={theme.colors["accent-soft"]} />
      <Circle cx="292" cy="56" r="22" fill={theme.colors.surface} />
      <Path
        d="M56 196C58 151 101 82 160 73C221 64 249 117 303 107"
        fill="none"
        stroke={color}
        strokeWidth="2"
        strokeDasharray="5 8"
        opacity=".32"
      />
      <G rotation="-8" origin="180,144">
        <Rect
          x="82"
          y="82"
          width="192"
          height="138"
          rx="20"
          fill={theme.colors.surface}
          stroke={theme.colors.border}
        />
        <Path
          d="M178 104V199M106 111C131 106 150 111 165 119M106 128C128 122 149 128 165 134M195 119H248M195 137H238"
          stroke={color}
          strokeWidth="3"
          strokeLinecap="round"
          opacity=".55"
        />
        <Path
          d="M106 170L124 151L145 178L165 151"
          stroke={color}
          strokeWidth="3"
          strokeLinecap="round"
          strokeLinejoin="round"
          fill="none"
        />
        <Circle
          cx="215"
          cy="173"
          r="13"
          fill={theme.colors["accent-soft"]}
          stroke={color}
          strokeWidth="2"
        />
        <Line
          x1="228"
          y1="173"
          x2="248"
          y2="173"
          stroke={color}
          strokeWidth="2"
        />
      </G>
      <Rect x="258" y="190" width="52" height="52" rx="17" fill={color} />
      <Path
        d="M273 216L281 224L295 207"
        stroke={theme.colors.onAccent}
        strokeWidth="3.5"
        strokeLinecap="round"
        strokeLinejoin="round"
        fill="none"
      />
      <Path
        d="M62 61L65 70L74 73L65 76L62 85L59 76L50 73L59 70Z"
        fill={color}
      />
      <Circle cx="293" cy="56" r="5" fill={color} />
    </Svg>
  );
}
