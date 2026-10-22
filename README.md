# Surgical-Assistant
## System Overview

This system provides real-time formal safety monitoring for laparoscopic cholecystectomy procedures. It processes endoscopic video frames through a four-stage pipeline that detects surgical tools, tracks them persistently across frames, preprocesses the tracking signals, and verifies them against formal safety constraints.

### Pipeline

**Video Frame → Tool Detection → Tracking & Re-ID → Signal Preprocessing → Formal Runtime Monitor → Safe / Violation**

---

### 1. Surgical Video Input and Tool Detection

The system processes sequential endoscopic video frames using a YOLOv12-x detector. For each frame, the detector identifies surgical instruments, estimates their spatial locations, and outputs the tool class, bounding box, and confidence score. The resulting detections form the input to the tracking stage.

<p align="center">
  <a href="image/yoloinputtooutput.png">
    <img src="image/yoloinputtooutput.png" alt="YOLOv12-X Input-to-Output Pipeline" width="1000"/>
  </a>
</p>

### 2. Tracking and Re-Identification (DeepSORT LogicAdapter)

YOLOv12-x processes frames independently and does not maintain persistent tool identities across frames. Moreover, missed detections may falsely indicate tool absence, leading to incorrect safety violations. The DeepSORT LogicAdapter addresses these issues by associating detections across consecutive frames, maintaining persistent tool trajectories, and handling short-term detection gaps. This prevents transient detection failures from propagating as false tool absences to subsequent pipeline stages.

## DeepSORT Architecture and Tracking

## DeepSORT Architecture and Tracking

<table>
  <tr>
    <td align="center" width="40%">
      <a href="image/deepsort.png">
        <img src="image/deepsort.png" alt="DeepSORT Architecture" width="100%"/>
      </a>
      <br/>
      <b>DeepSORT Architecture</b>
    </td>
    <td align="center" width="60%">
      <a href="image/deepsortkeptframe.png">
        <img src="image/deepsortkeptframe.png" alt="DeepSORT Kept Frames" width="100%"/>
      </a>
      <br/>
      <b>DeepSORT Kept Frames</b>
    </td>
  </tr>
</table>


The output is a set of persistent tracks, each containing a track ID, tool class, tool name, bounding box, confidence, and a reported state. The reported state is determined by exponential moving-average smoothing with hysteresis thresholds, so that a track is only reported as present once its score exceeds an upper threshold and only cleared once it falls below a lower one.


### 3. Post-Tracking Signal Preprocessing

Since multiple tracks may represent the same physical tool over time, the per-track reported flags are aggregated at the tool-class level — a class is marked active if any of its tracks is reported. The resulting binary signals are smoothed over a sliding window, and a class is passed to the formal monitor as active only when its active-frame fraction meets the class-specific threshold. This step reduces transient fluctuations and provides consistent binary tool-presence signals.

**Example:** Consider a tool class whose DeepSORT track-level reported flags over seven consecutive frames are (T, F, F, T, F, T, T). After class-level aggregation and sliding-window smoothing, the post-tracking preprocessor converts these into temporally consistent binary tool-presence signals (1, 1, 0, 1, 0, 1, 1). Whether a transient absence (such as the second frame) is corrected to active or preserved as inactive depends on the class-specific threshold applied to the sliding window.

For instance, consider a Bipolar detected for 13 consecutive frames, followed by a four-frame occlusion. At the fourth occluded frame, the 12-frame window contains eight active and four inactive frames, giving an active-frame fraction of 8/12 ≈ 0.67. Since this exceeds the Bipolar threshold of 0.6, the smoothed output remains T2 = 1, preventing the temporary occlusion from being interpreted as tool absence. However, for the Irrigator under the same conditions, the fraction is below its stricter threshold of 0.9, so the smoothed output becomes T7 = 0.

The resulting Boolean signals are then passed to the formal runtime monitor for verification of surgical tool usage sequences and temporal constraints.

### 4. Formal Runtime Monitoring

An offline process extracts safety rules in Signal Temporal Logic (STL) from expert surgical recordings, complemented by clinical guidelines manually formalized by expert surgeons. These rules specify safety constraints such as permitted tool-use sequences and duration limits.

The resulting rules are used to synthesize a runtime monitor deployed after the signal preprocessor. It checks incoming binary tool-presence signals against the safety constraints and reports either a **safe state** or a **violation**, which is recorded with the corresponding frame for further analysis.

## DeepSORT Tracking Module

This module implements a customized DeepSORT-based multi-object tracking pipeline for surgical tool tracking in laparoscopic cholecystectomy videos. It combines YOLOv12x detection with appearance-based re-identification and temporal smoothing to produce stable, identity-consistent tool tracks across video frames.

### Tracked Surgical Tools

Grasper, Bipolar, Hook, Scissors, Clipper, Irrigator, and SpecimenBag.

### Pipeline Stages

**Frame → YOLO Detection → Appearance Embedding → DeepSORT Association → Class Stabilization → Temporal Smoothing → Output**

**1. Detection & Preprocessing:**
Each frame is passed through a trained YOLOv10x model. Low-confidence and very small detections are filtered out. For each valid detection, an image crop is extracted for appearance feature computation.

**2. Appearance Feature Extraction:**
Each detected tool crop is passed through a pretrained MobileNetV2 backbone (classification head removed) to obtain a compact appearance embedding. This embedding acts as a visual fingerprint, enabling the tracker to distinguish between multiple tools of the same class. A color-histogram fallback is used when deep features are unavailable.

**3. Detection-to-Track Association:**
A hybrid cost matrix is computed for all track–detection pairs, combining appearance similarity (cosine distance) and spatial overlap (IoU). The Hungarian algorithm finds the globally optimal assignment. Unmatched detections are checked against a short-term memory of recently lost tracks for re-identification — if the appearance is sufficiently similar and the class matches, the original track ID is preserved instead of creating a new one.

**4. Class Label Stabilization:**
To suppress single-frame misclassifications, the tracker maintains a short rolling history of predicted classes per track and applies majority voting before switching the label.

**5. Temporal Smoothing:**
An EMA-based smoother with hysteresis thresholds controls tool presence reporting. This prevents flickering — tools are not reported from a single noisy detection, and brief disappearances do not interrupt an active report.

**6. Output:**
Each frame produces a list of active tracks, each containing a unique persistent ID, stabilized tool name, bounding box, confidence score, and a flag indicating whether the tool is reliably present.
