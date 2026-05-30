// Remotion configuration for the daily-video-agent title card.
// Portrait output, transparent capable codec for clean overlay if needed.
import { Config } from "@remotion/cli/config";

Config.setVideoImageFormat("png");
Config.setConcurrency(2);
Config.setOverwriteOutput(true);
// ProRes 4444 keeps a clean alpha capable master that ffmpeg can re encode later.
Config.setCodec("prores");
Config.setProResProfile("4444");
