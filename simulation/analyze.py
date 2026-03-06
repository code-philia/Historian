import os
import json
from dotenv import load_dotenv

load_dotenv()

if __name__ == "__main__":
    output_dir = os.getenv("OUTPUT_DIR")
    sut = "Claude"
    fk = 0
    fj = 0
    fb = 0
    fr = 0
    tp = 0
    fp = 0
    fn = 0
    simulated_file_num = 0
    for output in os.listdir(output_dir):
        if f"-{sut}-simulation-results.json" not in output:
            continue
        with open(os.path.join(output_dir, output), "r") as f:
            data = json.load(f)

        simulated_file_num += 1
        for record in data["SUT_prediction_records"][1:]:
            ev = record["evaluations"]
            if "flow_keeping" in ev:
                fk += len(ev["flow_keeping"])
                fj += len(ev["flow_jumping"])
                fb += len(ev["flow_breaking"])
                fr += len(ev["flow_reverting"])
            tp += ev["tp@all"]
            fp += ev["fp@all"]
            fn += ev["fn@all"]
            

    print(f"Simulated file num: {simulated_file_num}")
    total_flow = fk + fj + fb + fr
    if total_flow > 0:
        print(f"Flow-keeping: {fk/total_flow*100:.2f}%")
        print(f"Flow-jumping: {fj/total_flow*100:.2f}%")
        print(f"Flow-breaking: {fb/total_flow*100:.2f}%")
        print(f"Flow-reverting: {fr/total_flow*100:.2f}%")
    else:
        print("Flow analysis: N/A (flow deactivated)")
    print(f"TP: {tp}, FP: {fp}, FN: {fn}")
    precision = tp/(tp+fp)*100
    print(f"Precision: {precision:.2f}%")
    recall = tp/(tp+fn)*100
    print(f"Recall: {recall:.2f}%")
    f1 = 2*tp/(2*tp+fp+fn)*100
    print(f"F1: {f1:.2f}%")
