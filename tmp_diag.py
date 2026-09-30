import json, numpy as np, sys
sys.path.insert(0,".")
from core.canonical import COCO17, COCO_INDEX, FramePose
from core.lifter import AnalyticLifter

d=json.load(open("storage/jobs/e63f073c2149/kp2d.json"))
W,H,FPS=d["width"],d["height"],d["fps"]
frames=[]
for row in d["frames"]:
    a=np.array(row,dtype=np.float64)
    frames.append(FramePose(a[:,:2], a[:,2], None, W, H))
lifted=AnalyticLifter().lift(frames)
t=int(round(1.19*FPS))
print("frame",t,"of",len(frames))
def ang(a,b,c):
    v1=a-b; v2=c-b
    v1=v1/np.linalg.norm(v1); v2=v2/np.linalg.norm(v2)
    return np.degrees(np.arccos(np.clip(np.dot(v1,v2),-1,1)))
for lbl,idx in (("2D",None),("3D",1)):
    print("=== ",lbl)
    for side in ("left","right"):
        sh=COCO_INDEX[side+"_shoulder"]; el=COCO_INDEX[side+"_elbow"]; wr=COCO_INDEX[side+"_wrist"]
        f=frames[t] if idx is None else lifted[t]
        a,b,c=f.kp2d[sh],f.kp2d[el],f.kp2d[wr]
        if idx: a,b,c=f.kp3d[sh],f.kp3d[el],f.kp3d[wr]
        print(f" {side:5s} elbow_angle={ang(a,b,c):6.2f}  sh={np.round(a,3)} el={np.round(b,3)} wr={np.round(c,3)}")
# depth info
k=lifted[t].kp3d
ci=COCO_INDEX
print("hips", np.round(0.5*(k[ci["left_hip"]]+k[ci["right_hip"]]),3))
print("chest", np.round(0.5*(k[ci["left_shoulder"]]+k[ci["right_shoulder"]]),3))
for side in ("left","right"):
    wr=k[ci[side+"_wrist"]]; sh=k[ci[side+"_shoulder"]]; el=k[ci[side+"_elbow"]]
    print(f" {side}: z_sh={sh[2]:+.3f} z_el={el[2]:+.3f} z_wr={wr[2]:+.3f}  (z_wr - z_chest={wr[2]-k[ci['left_shoulder']][2]:+.3f})")
