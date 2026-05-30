// Animated title card component.
// Fades and slides in a title plus a small date subtitle on a dark background.
import React from "react";
import {
  AbsoluteFill,
  interpolate,
  spring,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";

type TitleCardProps = {
  title: string;
  subtitle: string;
};

export const TitleCard: React.FC<TitleCardProps> = ({title, subtitle}) => {
  const frame = useCurrentFrame();
  const {fps, durationInFrames} = useVideoConfig();

  // Spring driven entrance for the title.
  const enter = spring({
    frame,
    fps,
    config: {damping: 200, mass: 0.6},
  });
  const titleY = interpolate(enter, [0, 1], [60, 0]);
  const titleOpacity = interpolate(enter, [0, 1], [0, 1]);

  // Subtitle eases in slightly after the title.
  const subOpacity = interpolate(frame, [10, 28], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  // Gentle fade out at the very end so the cut into the main video is soft.
  const fadeOut = interpolate(
    frame,
    [durationInFrames - 10, durationInFrames],
    [1, 0],
    {extrapolateLeft: "clamp", extrapolateRight: "clamp"}
  );

  // Accent underline grows from the center.
  const underlineWidth = interpolate(enter, [0, 1], [0, 320]);

  return (
    <AbsoluteFill
      style={{
        backgroundColor: "#0B0B0F",
        justifyContent: "center",
        alignItems: "center",
        opacity: fadeOut,
        fontFamily:
          "Inter, -apple-system, BlinkMacSystemFont, Helvetica, Arial, sans-serif",
      }}
    >
      <div
        style={{
          transform: `translateY(${titleY}px)`,
          opacity: titleOpacity,
          textAlign: "center",
          padding: "0 80px",
        }}
      >
        <div
          style={{
            color: "#FFFFFF",
            fontSize: 110,
            fontWeight: 800,
            lineHeight: 1.05,
            letterSpacing: -2,
          }}
        >
          {title}
          <span style={{color: "#22C55E"}}>.</span>
        </div>

        <div
          style={{
            width: underlineWidth,
            height: 8,
            backgroundColor: "#22C55E",
            borderRadius: 4,
            margin: "40px auto 0",
          }}
        />

        <div
          style={{
            marginTop: 40,
            color: "#9CA3AF",
            fontSize: 44,
            fontWeight: 500,
            opacity: subOpacity,
            letterSpacing: 2,
          }}
        >
          {subtitle}
        </div>
      </div>
    </AbsoluteFill>
  );
};
