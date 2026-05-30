// Root composition registry for the daily-video-agent title card.
// Defines one composition "Title" at portrait 1080x1920, 30fps.
import React from "react";
import {Composition} from "remotion";
import {TitleCard} from "./TitleCard";

// Roughly 2.5 seconds at 30fps for a short, punchy intro card.
const FPS = 30;
const DURATION_IN_FRAMES = 75;
const WIDTH = 1080;
const HEIGHT = 1920;

export const RemotionRoot: React.FC = () => {
  return (
    <Composition
      id="Title"
      component={TitleCard}
      durationInFrames={DURATION_IN_FRAMES}
      fps={FPS}
      width={WIDTH}
      height={HEIGHT}
      defaultProps={{
        title: "Daily Video",
        subtitle: new Date().toISOString().slice(0, 10),
      }}
    />
  );
};
