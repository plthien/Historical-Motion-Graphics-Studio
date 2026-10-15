import React from 'react';
import {Composition} from 'remotion';
import {DocumentaryComposition} from './DocumentaryComposition.jsx';
import {motionPlan} from './plan.mjs';

export const RemotionRoot = () => <Composition
  id="HistoryPOC"
  component={DocumentaryComposition}
  width={motionPlan.width}
  height={motionPlan.height}
  fps={motionPlan.fps}
  durationInFrames={motionPlan.durationFrames}
  defaultProps={{plan: motionPlan}}
/>;
