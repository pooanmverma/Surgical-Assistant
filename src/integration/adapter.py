from collections import deque, defaultdict, Counter
import time
import os
import logging

import numpy as np
import cv2

try:
    import torch
    import torchvision
    from torchvision import transforms
    TORCH_AVAILABLE = True
except Exception:
    TORCH_AVAILABLE = False

try:
    from scipy.optimize import linear_sum_assignment
    SCIPY_AVAILABLE = True
except Exception:
    SCIPY_AVAILABLE = False

from ultralytics import YOLO

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("DeepSORTLogicAdapter")

DEFAULT_MODEL = './runs/official_detect/yolov12x_official_split_baseline_new2/weights/best.pt'
DEFAULT_CONF_THRESHOLD = 0.4
DEFAULT_REID_BACKBONE = 'mobilenet_v2'

TOOL_MAPPING = {
    0: "Grasper",
    1: "Bipolar",
    2: "Hook",
    3: "Scissors",
    4: "Clipper",
    5: "Irrigator",
    6: "SpecimenBag"
}

CLASS_HISTORY_LEN = 7
CLASS_CHANGE_DEFAULT = {'frac': 0.6, 'min_count': 3}
# Irrigator (5) gets stricter thresholds — it's the most commonly misclassified in the confusion matrix
CLASS_SMOOTH_OVERRIDES = {
    5: {'frac': 0.9, 'min_count': 4}
}


def iou_bbox(a, b):   #BAAD M DEEPMATCHER M USE HOTA HAI/OUTPUT OF THIS FUNCTION GROUND TRUTH AND PERDICTION KITNE SIMILAR HAI
    x1 = max(a[0], b[0]); y1 = max(a[1], b[1]); x2 = min(a[2], b[2]); y2 = min(a[3], b[3])
    inter_w = max(0, x2 - x1 + 1); inter_h = max(0, y2 - y1 + 1)
    inter = inter_w * inter_h
    area_a = max(0, a[2] - a[0] + 1) * max(0, a[3] - a[1] + 1)
    area_b = max(0, b[2] - b[0] + 1) * max(0, b[3] - b[1] + 1)
    denom = area_a + area_b - inter
    return 0.0 if denom <= 0 else inter / denom


def cxcywh_from_xyxy(b):#YOLO GIVES THIS FORMAT X1,Y1,X2,Y2 BUT TRACKER NEEDS DIFFERENT FORMAT CENTER X CENTRE Y  WIDTH,HEIGHT
    x1, y1, x2, y2 = b
    w = x2 - x1; h = y2 - y1
    return (x1 + w / 2.0, y1 + h / 2.0, w, h)


class AppearanceExtractor:
    """Extract a compact appearance embedding for a cropped image patch.
#YOLO ONLY TELLS THIS GRASPER BUT DEEPSORT TELL US KYA YE WAHI WLA PURANA GRASPER HAI,sliye ye class har detected tool ka appearance feature (embedding) nikalti hai.
    Preferred: MobileNetV2 (torchvision pretrained). Fallback to color histogram when torch unavailable.
    """
    def __init__(self, device='cpu', backbone='mobilenet_v2'):
        self.device = device
        self.backbone = backbone
        self.enabled = False
        if TORCH_AVAILABLE:
            try:
                if backbone == 'mobilenet_v2':
                    model = torchvision.models.mobilenet_v2(pretrained=True)
                    self.feature_extractor = torch.nn.Sequential(
                        *list(model.features.children()),#CLASSFICATION LAYER HTA DI HAI
                        torch.nn.AdaptiveAvgPool2d((1, 1))
                    )
                    self.feature_size = 1280 #HAR TOOL 1280 AE REPERESNT HOGA
                else:
                    model = torchvision.models.resnet50(pretrained=True)# THIS IS OPTION NOT USED ANYWHERE
                    self.feature_extractor = torch.nn.Sequential(*list(model.children())[:-1])
                    self.feature_size = 2048
                self.feature_extractor.eval()
                try:
                    self.feature_extractor.to(self.device)
                except Exception as e:
                    logger.warning("Could not move feature extractor to %s: %s", self.device, e)
                self.tf = transforms.Compose([
                    transforms.ToPILImage(),#IMAGE PERPROCESSING
                    transforms.Resize((128, 128)),#SAME
                    transforms.ToTensor(),#SAME
                    transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225])
                ])
                self.enabled = True
                logger.info("Appearance extractor enabled (backbone=%s) on device=%s", backbone, self.device)
            except Exception as e:
                logger.warning("Appearance extractor init failed; falling back to histogram: %s", e)
        else:
            logger.info("Torch/torchvision not available — using histogram-based appearance fallback")

    def extract(self, image_patch):
        """Return a normalized 1D float32 embedding vector."""
        if self.enabled:
            try:
                x = self.tf(image_patch).unsqueeze(0).to(self.device)#IMAGE IS PERPARED BEFORE PASS IT TO MOBILE NET
                with torch.no_grad(): #NO TRAINING
                    f = self.feature_extractor(x)#NOW WE ENTER IN MOBILENET OUTPUT OF THIS IS 1280 DIMENSIONAL EMBEDDING
                f = f.cpu().numpy().reshape(-1)#MODEL NE TENSOR DIA USKO COVERT KIA NUMPY (-1) MEANS IF SHAPE 1*1280*1*1 THAN CHANGE IT INTO 1280
                n = np.linalg.norm(f)#NORMLIZATION OF VECTOR
                return (f / n).astype(np.float32) if n > 0 else f.astype(np.float32)
            except Exception as e:
                logger.debug("Appearance extraction failed, using histogram fallback: %s", e)
        try:
            hsv = cv2.cvtColor(image_patch, cv2.COLOR_BGR2HSV)#THIS ONOLY COUNT THE COLORS OF IMAGE
            h = cv2.calcHist([hsv], [0, 1, 2], None, [8, 4, 4], [0, 180, 0, 256, 0, 256])
            h = cv2.normalize(h, h).flatten()
            n = np.linalg.norm(h)
            return (h / n).astype(np.float32) if n > 0 else h.astype(np.float32)
        except Exception as e:
            logger.debug("Histogram fallback failed: %s", e)
            return np.zeros(128, dtype=np.float32)# IF HISTOGRAM ALSO FAIL
#EMBEDDING BNAKR KISKO PASS KI DEEPMATCHER

class DeepMatcher:#COMPARE 2 EMBEDDINGS karega aur decide karega ki same track continue karni hai ya nayi track banani hai.DeepMatcher ki settings (configuration) store karta hai.
    """Match detections to tracks using a hybrid cost: weighted appearance distance + (1 - IoU).
    Uses Hungarian assignment when scipy is available; greedy fallback otherwise.
    """
    def __init__(self, appearance_weight=0.6, iou_weight=0.4, max_cosine_distance=0.4):
        self.appearance_weight = appearance_weight
        self.iou_weight = iou_weight
        self.max_cosine_distance = max_cosine_distance  #Ye constructor sirf parameters store karta hai.

    def cosine_distance(self, a, b):#CONSINE DISTANCE JITNA JAYADA HOGA UTHNE OBJECT DIFFERENT HOGE
        if a is None or b is None:
            return 1.0
        denom = np.linalg.norm(a) * np.linalg.norm(b)
        if denom <= 0:
            return 1.0
        return max(0.0, 1.0 - np.dot(a, b) / denom)

    def compute_cost_matrix(self, tracks, detections):#only made metrix
        N = len(tracks); M = len(detections)
        if N == 0 or M == 0:
            return np.zeros((N, M), dtype=np.float32)
        cost = np.zeros((N, M), dtype=np.float32)
        for i, t in enumerate(tracks):
            for j, d in enumerate(detections):
                iouv = iou_bbox(t.bbox, d['bbox'])
                app_dist = self.cosine_distance(
                    getattr(t, 'last_embedding', None), d.get('embedding')
                )
                if app_dist > self.max_cosine_distance:
                    cost[i, j] = 1.0
                else:
                    cost[i, j] = self.appearance_weight * app_dist + self.iou_weight * (1.0 - iouv)
        return cost

    def match(self, tracks, detections, iou_threshold=0.3):
        cost = self.compute_cost_matrix(tracks, detections)#Cost Matrix ban rahi hai.
        matched = []
        if cost.size > 0:
            if SCIPY_AVAILABLE:
                row_ind, col_ind = linear_sum_assignment(cost)#hungarian algo.apply on cost metrix
                matched = [(r, c) for r, c in zip(row_ind, col_ind) if cost[r, c] <= 0.999]#why this 0.999
            else:
                cost_copy = cost.copy()
                while True:
                    r, c = np.unravel_index(np.argmin(cost_copy), cost_copy.shape)
                    if cost_copy[r, c] >= 1.0:
                        break
                    matched.append((r, c))
                    cost_copy[r, :] = 1.0
                    cost_copy[:, c] = 1.0
        matched_rows = {m[0] for m in matched}
        matched_cols = {m[1] for m in matched}
        unmatched_tracks = [i for i in range(len(tracks)) if i not in matched_rows]
        unmatched_dets = [i for i in range(len(detections)) if i not in matched_cols]
        return matched, unmatched_tracks, unmatched_dets

#match()compute_cost_matrix() Cost Matrix linear_sum_assignment()Hungarian Algorithm Best Matching matched list
class Track:
    def __init__(self, bbox, cls_id, track_id, max_history=30):#Jab naya object pehli baar detect hota hai tab ye constructor call hota hai.
        self.track_id = track_id
        self.cls_id = cls_id
        self.bbox = bbox
        self.hits = 1 #Kitni baar ye object successfully match hua.
        self.time_since_update = 0 #Kitne frames se object update nahi hua.
        self.history = deque(maxlen=max_history)
        self.history.append(bbox)
        self.last_embedding = None #Appearance extractor abi avialable ni hai
        self.last_update_ts = time.time()
        self.lost = False #object lost ni hua
        self.class_history = deque(maxlen=CLASS_HISTORY_LEN)
        self.class_history.append(cls_id)
        self._just_lost = False #Object abhi-abhi lost hua?


    def predict(self): #yeh chlta jb tracker detect na kr paye yeh only next object kha ho skta hai use detect krta hai
        if len(self.history) >= 2:#Agar ek frame me YOLO object detect nahi kare
            x1, y1, x2, y2 = self.history[-1] #latest bbox
            x1p, y1p, _, _ = self.history[-2] #last  bbox do bbox se estimation lga skta hai
            dx = x1 - x1p; dy = y1 - y1p
            self.bbox = [int(x1 + dx), int(y1 + dy), int(x2 + dx), int(y2 + dy)]
        return self.bbox

    def update(self, bbox, embedding=None, cls_id=None): #Ye tab call hota hai jab Hungarian Algorithm kisi detection ko is track ke saath successfully match kar deta hai.
        self.history.append(bbox) #perdiction function bolta hai 120 next frame postion but when again yolo detect krta to let suppopse 122 to need update
        self.bbox = bbox
        if embedding is not None:
            self.last_embedding = embedding
        if cls_id is not None:
            self.class_history.append(cls_id)
        self.hits += 1
        self.time_since_update = 0
        self.last_update_ts = time.time()
        self.lost = False
        self._just_lost = False

    def mark_missed(self):
        self.time_since_update += 1
        self._just_lost = self.time_since_update == 1
        self.lost = True


class DeepSORTLogicAdapter:
    """YOLO detection + DeepSORT-style association + per-track EMA smoothing.

    Public interface: process_frame(frame, frame_idx=None) -> list of per-track dicts.
    Each dict: {'track_id': int, 'class_id': int, 'tool_name': str,
                'bbox': [x1,y1,x2,y2], 'conf': float, 'reported': bool}
    """
    def __init__(self, model_path, tool_mapping=TOOL_MAPPING, conf_thresh=DEFAULT_CONF_THRESHOLD,
                 device='cpu', reid_backbone=DEFAULT_REID_BACKBONE,
                 appearance_weight=0.6, iou_weight=0.4, max_cosine_distance=0.45,
                 max_age=30, min_hits=1, iou_match_threshold=0.3,
                 smoother_cfg=None, class_smooth_overrides=None): #ek hi baar execute hoga.Uske baad video ke har frame ke liye process_frame() call hoga. anf tool mapping jo yolo classid dega us class tool ka naam degi.max_age=Object agar kuch frames ke liye disappear ho jayeTo track kitni der tak zinda rahe?
        if not os.path.exists(model_path):
            raise FileNotFoundError(model_path)
        self.model = YOLO(model_path)
        self.tool_mapping = tool_mapping
        self.conf_thresh = conf_thresh
        self.device = device

        if self.device == 'cuda':
            torch.backends.cudnn.benchmark = True
            torch.backends.cudnn.deterministic = False
            if TORCH_AVAILABLE and not torch.cuda.is_available():
                logger.warning("CUDA requested but not available — falling back to CPU")
                self.device = 'cpu'

        try:
            if hasattr(self.model, 'to'):
                self.model.to(self.device)
                logger.info("YOLO model moved to device: %s", self.device)
        except Exception as e:
            logger.warning("Failed to move YOLO model to %s: %s", self.device, e)

        self.appearance = AppearanceExtractor(device=self.device, backbone=reid_backbone)
        self.matcher = DeepMatcher(
            appearance_weight=appearance_weight,
            iou_weight=iou_weight,
            max_cosine_distance=max_cosine_distance
        )
        self.tracks = [] #bacuse tracking not yet start before all set all object are create
        self.next_id = 1 # har nye object ko new id dene ke lye,aur yeh kbhi bhi reuse ni hota
        self.max_age = max_age #track kb tk zinda rhe ga after creation if yolo miss
        self.min_hits = min_hits
        self.iou_match_threshold = iou_match_threshold

        sc = smoother_cfg or {}#Agar key mile to uski value use karo, warna default value use karo
        self.smoother = PerTrackSmoother(
            fps=sc.get('fps', 30.0),#default values of sc

            ema_alpha=sc.get('ema_alpha', 0.4),
            present_th=sc.get('present_th', 0.5),
            absent_th=sc.get('absent_th', 0.35),
            grace_s=sc.get('grace_s', 0.08),
            min_pres_s=sc.get('min_pres_s', 0.03)
        )
        self.class_smooth_overrides = class_smooth_overrides or CLASS_SMOOTH_OVERRIDES

        self.lost_tracks_memory = {}
        self.reid_memory_ttl = 5.0 #Lost track kitni der memory me rahega.
        self.counters = defaultdict(int)

    def _preprocess_detections(self, frame, results):#Ye function YOLO ke raw output ko DeepSORT ke format me convert karta hai.
        dets = []
        if not (results and len(results) > 0):
            return dets
        boxes = results[0].boxes
        if boxes is None:
            return dets
        for box in boxes:
            try:
                cls_id = int(box.cls[0])
                conf = float(box.conf[0])
                xyxy = box.xyxy[0].cpu().numpy() if hasattr(box.xyxy[0], 'cpu') else np.array(box.xyxy[0])
                x1, y1, x2, y2 = [int(v) for v in xyxy]
            except Exception:
                data = np.array(box)[0]
                x1, y1, x2, y2 = [int(v) for v in data[:4]]
                conf = float(data[4]); cls_id = int(data[5])
            if conf < self.conf_thresh:
                continue
            if max(1, x2 - x1) * max(1, y2 - y1) < 400:
                continue
            embedding = None
            try:
                crop = frame[max(0, y1):max(0, y2), max(0, x1):max(0, x2)]
                if crop.size > 0:
                    embedding = self.appearance.extract(crop)
            except Exception as e:
                logger.debug("Embedding extraction error: %s", e)
            dets.append({'bbox': [x1, y1, x2, y2], 'cls_id': cls_id, 'conf': conf, 'embedding': embedding})

        if logger.isEnabledFor(logging.DEBUG):
            logger.debug("Detections: %s", [(d['cls_id'], round(d['conf'], 2)) for d in dets])
        return dets
#Frame YOLO Results Read every box  Confidence Filtes small Box Filter Crop Image Extract Embedding Create Detection Dictionary Return Detection List
    def _associate(self, detections, frame_idx=None):
        for t in self.tracks:
            t.predict()

        matched, unmatched_tracks_idx, unmatched_det_idx = self.matcher.match(self.tracks, detections)

        for tr_idx, det_idx in matched:
            tr = self.tracks[tr_idx]
            det = detections[det_idx]
            tr.update(det['bbox'], embedding=det.get('embedding'), cls_id=det['cls_id'])
            if tr.track_id in self.lost_tracks_memory:
                del self.lost_tracks_memory[tr.track_id]

        for tr_idx in unmatched_tracks_idx:
            self.tracks[tr_idx].mark_missed()
            tr = self.tracks[tr_idx]
            # log only on transition into lost to avoid log spam
            if tr.lost and tr._just_lost and tr.last_embedding is not None:
                self.lost_tracks_memory[tr.track_id] = {
                    'embedding': tr.last_embedding, 'bbox': tr.bbox,
                    'ts': time.time(), 'cls': tr.cls_id
                }
                logger.info("TRACK LOST: ID=%d cls=%s frame=%s", tr.track_id, tr.cls_id, frame_idx)

        for det_idx in unmatched_det_idx: #jb detection kisi track se match ni hui
            det = detections[det_idx]
            assigned = False #Ye detection kisi bhi purane track ki nahi hai.
            if det.get('embedding') is not None: #check karta hai ki detection ki appearance feature available hai ya nahi.Kyunki Re-ID appearance se hota hai.
                now = time.time()
                for lost_id, info in list(self.lost_tracks_memory.items()):# Ab har lost track ko current detection ke saath compare kiya jayega.
                    if now - info['ts'] > self.reid_memory_ttl: #track lost hua and now current time is less than 5 seconds so yh bhot purani memory ni hai
                        del self.lost_tracks_memory[lost_id] #purnane track ko delete kr do
                        continue
                    # only re-link same class to avoid ghost associations across tool types
                    if info['cls'] != det['cls_id']: #track ke baad class bhi check hogi
                        continue
                    denom = np.linalg.norm(det['embedding']) * np.linalg.norm(info['embedding'])
                    sim = np.dot(det['embedding'], info['embedding']) / denom if denom > 0 else 0.0 #consine similarity check ho rhi hai
                    if sim > 0.75:
                        tr = Track(det['bbox'], det['cls_id'], lost_id)
                        tr.last_embedding = det.get('embedding')#Current detection ki embedding save kar di.
                        tr.class_history.append(det['cls_id'])
                        self.tracks.append(tr)#Ab revived track fir se active list me aa gaya.
                        assigned = True
                        del self.lost_tracks_memory[lost_id]
                        self.counters['reid_revives'] += 1
                        logger.info("RE-ID REVIVE: track=%d sim=%.2f frame=%s", lost_id, sim, frame_idx)
                        break
            if not assigned:
                tr_new = Track(det['bbox'], det['cls_id'], self.next_id) #new track bna
                tr_new.last_embedding = det.get('embedding')
                tr_new.class_history.append(det['cls_id'])#Naye track ki class history shuru hoti hai.
                self.counters['new_tracks'] += 1
                logger.info("NEW TRACK: ID=%d class=%s conf=%.2f frame=%s",
                            self.next_id, det['cls_id'], det['conf'], frame_idx)
                self.next_id += 1
                self.tracks.append(tr_new)

        surviving = []
        for tr in self.tracks:
            if tr.time_since_update <= self.max_age:
                surviving.append(tr)
            else:
                # retain snapshot for re-ID if the tool re-enters frame
                if tr.last_embedding is not None:#Track active nahi rahega.Lekin delete karne se pehleUski information save karni hai.
                    self.lost_tracks_memory[tr.track_id] = {
                        'embedding': tr.last_embedding, 'bbox': tr.bbox,
                        'ts': time.time(), 'cls': tr.cls_id
                    }
                self.counters['aged_out'] += 1
                logger.info("TRACK AGED OUT: ID=%d frame=%s", tr.track_id, frame_idx)
        self.tracks = surviving

    def _stabilize_track_classes(self):#class stable krta hai
        """Switch canonical class only when the new class dominates recent history by the
        configured fraction. Suppresses single-frame misclassifications.
        """
        for tr in self.tracks:
            hist = list(tr.class_history)
            if not hist:
                continue
            mode, cnt = Counter(hist).most_common(1)[0]
            if mode == tr.cls_id:
                continue
            params = self.class_smooth_overrides.get(tr.cls_id, CLASS_CHANGE_DEFAULT)
            if cnt / len(hist) >= params['frac'] and cnt >= params['min_count']:
                old = tr.cls_id
                tr.cls_id = mode
                logger.info("CLASS SWITCH: track=%d %s -> %s (frac=%.2f cnt=%d)",
                            tr.track_id, TOOL_MAPPING.get(old, old),
                            TOOL_MAPPING.get(mode, mode), cnt / len(hist), cnt)

    def process_frame(self, frame, frame_idx=None):
        """Run detection, tracking, and smoothing for one frame.

        Returns:
            list of dicts, one per active track:
            {'track_id': int, 'class_id': int, 'tool_name': str,
             'bbox': [x1,y1,x2,y2], 'conf': float, 'reported': bool}
        """
        try:
            if self.device == 'cuda':
                with torch.cuda.amp.autocast(enabled=True):
                    results = self.model(frame, verbose=False)#actual YOLO model run hota hai.
            else:
                results = self.model(frame, verbose=False)
        except Exception as e:
            logger.exception("YOLO inference error: %s", e)
            results = None

        dets = self._preprocess_detections(frame, results)#YOLO output ko tracker ke format me convert karta hai.
        self._associate(dets, frame_idx=frame_idx)
        self._stabilize_track_classes()

        # Best IoU-matched detection confidence per track this frame; 0 if no match
        track_conf_map = {}
        for tr in self.tracks:
            best_conf = max(
                (d['conf'] for d in dets if d['cls_id'] == tr.cls_id and iou_bbox(tr.bbox, d['bbox']) > 0.5),
                default=0.0
            )
            track_conf_map[tr.track_id] = best_conf

        if logger.isEnabledFor(logging.DEBUG):
            logger.debug("Track conf map: %s", {k: round(v, 3) for k, v in track_conf_map.items()})

        reported_states = self.smoother.update(track_conf_map)

        return [
            {
                'track_id': tr.track_id,
                'class_id': tr.cls_id,
                'tool_name': self.tool_mapping.get(tr.cls_id, f"C{tr.cls_id}"),
                'bbox': tr.bbox,
                'conf': float(track_conf_map.get(tr.track_id, 0.0)),
                'reported': bool(reported_states.get(tr.track_id, False))
            }
            for tr in self.tracks
        ]


class PerTrackSmoother:
    def __init__(self, fps=30.0, ema_alpha=0.4, present_th=0.5, absent_th=0.35,
                 grace_s=0.08, min_pres_s=0.03):
        self.fps = max(1.0, float(fps))
        self.ema_alpha = float(ema_alpha)
        self.present_th = float(present_th)
        self.absent_th = float(absent_th)
        self.grace_frames = max(1, int(round(grace_s * self.fps)))
        self.min_presence_frames = max(1, int(round(min_pres_s * self.fps)))

        self.ema_conf = defaultdict(float)
        self.presence_count = defaultdict(int)
        self.absent_count = defaultdict(int)
        self.reported = defaultdict(bool)

    def update(self, track_conf_map):
        reported_state = {}
        for tid, conf in track_conf_map.items():
            new_ema = self.ema_alpha * conf + (1.0 - self.ema_alpha) * self.ema_conf.get(tid, 0.0)
            self.ema_conf[tid] = new_ema

            if conf > 1e-6:
                self.presence_count[tid] += 1
                self.absent_count[tid] = 0
            else:
                self.absent_count[tid] += 1
                self.presence_count[tid] = 0

            cur = self.reported.get(tid, False)#Kya pichhle frame me ye track already reported tha?
            if cur:
                new = self.absent_count[tid] <= self.grace_frames and new_ema >= self.absent_th
            else:
                new = new_ema >= self.present_th or self.presence_count[tid] >= self.min_presence_frames

            # force off if EMA collapses — prevents stale "reported" on truly absent tools
            if new and new_ema < (self.absent_th * 0.5):
                new = False

            if logger.isEnabledFor(logging.DEBUG):
                logger.debug("SMOOTHER tid=%s conf=%.3f ema=%.3f -> reported=%s", tid, conf, new_ema, new)

            self.reported[tid] = new
            reported_state[tid] = new

        stale = [t for t, ac in self.absent_count.items() if ac > self.grace_frames * 400]
        for t in stale:
            self.ema_conf.pop(t, None)
            self.presence_count.pop(t, None)
            self.absent_count.pop(t, None)
            self.reported.pop(t, None)
        return reported_state


__all__ = ['DeepSORTLogicAdapter']


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--model', default=DEFAULT_MODEL)
    parser.add_argument('--video', default='./keras_frcnn/dataset/testdata/video20.mp4')
    parser.add_argument('--out', default='out_deepsort.mp4')
    parser.add_argument('--conf', type=float, default=DEFAULT_CONF_THRESHOLD)
    parser.add_argument('--device', default='cuda', choices=['cpu', 'cuda'])
    args = parser.parse_args()

    logger.info("Starting demo: model=%s video=%s device=%s", args.model, args.video, args.device)
    adapter = DeepSORTLogicAdapter(
        args.model, TOOL_MAPPING,
        conf_thresh=args.conf,
        device=args.device,
        smoother_cfg={'fps': 30.0, 'ema_alpha': 0.4, 'present_th': 0.5,
                      'absent_th': 0.35, 'grace_s': 0.08, 'min_pres_s': 0.03},
        class_smooth_overrides=CLASS_SMOOTH_OVERRIDES
    )

    cap = cv2.VideoCapture(args.video)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 640)
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 480)
    out = cv2.VideoWriter(args.out, cv2.VideoWriter_fourcc(*'mp4v'), fps, (w, h))

    frame_idx = 0
    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                break
            frame_idx += 1
            tracks = adapter.process_frame(frame, frame_idx=frame_idx)
            for t in tracks:
                x1, y1, x2, y2 = t['bbox']
                col = (0, 200, 0) if t['reported'] else (120, 120, 120)
                cv2.rectangle(frame, (x1, y1), (x2, y2), col, 2)
                cv2.putText(frame, f"ID:{t['track_id']} {t['tool_name']} {t['conf']:.2f}",
                            (x1, y1 - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.5, col, 1)
                if t['reported'] and t['conf'] < 0.01:
                    cv2.putText(frame, '[BUFFER]', (x1, y2 + 12),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 200, 200), 1)
            out.write(frame)
            if frame_idx % 30 == 0:
                active = [f"{t['tool_name']}#{t['track_id']}" for t in tracks if t['reported']]
                logger.info("Frame %d: tracks=%d active=%s counters=%s",
                            frame_idx, len(tracks), active, dict(adapter.counters))
    except KeyboardInterrupt:
        logger.info("Interrupted")
    finally:
        cap.release()
        out.release()
        logger.info("Done. Output -> %s", args.out)
