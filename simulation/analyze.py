import os
import json

if __name__ == "__main__":
    sut = "Claude"
    fk = 0
    fj = 0
    fb = 0
    fr = 0
    tp = 0
    fp = 0
    fn = 0
    # ------
    fk_fk = 0
    fk_fj = 0
    fk_fb = 0
    fk_fr = 0
    fk_tp = 0
    fk_fp = 0
    fk_fn = 0
    simulated_file_num = 0
    fk_file_num = 0
    for output in os.listdir("../output"):
        if f"-{sut}-" not in output:
            continue
        with open(os.path.join("../output", output), "r") as f:
            data = json.load(f)

        if "flow-keeper" not in output:
            simulated_file_num += 1
            for record in data["SUT_prediction_records"][1:]:
                fk += len(record["evaluations"]["flow_pattern"]["flow_keeping"])
                fj += len(record["evaluations"]["flow_pattern"]["flow_jumping"])
                fb += len(record["evaluations"]["flow_pattern"]["flow_breaking"])
                fr += len(record["evaluations"]["flow_pattern"]["flow_reverting"])
                tp += record["evaluations"]["tp"]
                fp += record["evaluations"]["fp"]
                fn += record["evaluations"]["fn"]
        else:
            fk_file_num += 1
            for record in data:
                fk_fk += len(record["flow_pattern"]["flow_keeping"])
                fk_fj += len(record["flow_pattern"]["flow_jumping"])
                fk_fb += len(record["flow_pattern"]["flow_breaking"])
                fk_fr += len(record["flow_pattern"]["flow_reverting"])
                fk_tp += record["tp"]
                fk_fp += record["fp"]
                fk_fn += record["fn"]
            

    print(f"Simulated file num: {simulated_file_num}")
    fk_percent = fk/(fk+fj+fb+fr)*100
    fj_percent = fj/(fk+fj+fb+fr)*100
    fr_percent = fr/(fk+fj+fb+fr)*100
    fb_percent = fb/(fk+fj+fb+fr)*100
    print(f"Flow-keeping: {fk_percent:.2f}%")
    print(f"Flow-jumping: {fj_percent:.2f}%")
    print(f"Flow-breaking: {fb_percent:.2f}%")
    print(f"Flow-reverting: {fr_percent:.2f}%")
    print(f"TP: {tp}, FP: {fp}, FN: {fn}")
    precision = tp/(tp+fp)*100
    print(f"Precision: {precision:.2f}%")
    recall = tp/(tp+fn)*100
    print(f"Recall: {recall:.2f}%")
    f1 = 2*tp/(2*tp+fp+fn)*100
    print(f"F1: {f1:.2f}%")

    print("="*20)
    print("Performance after flow keeper")
    print(f"Simulated file num: {fk_file_num}")
    fk_fk_percent = fk_fk/(fk_fk+fk_fj+fk_fb+fk_fr)*100
    fk_fj_percent = fk_fj/(fk_fk+fk_fj+fk_fb+fk_fr)*100
    fk_fb_percent = fk_fb/(fk_fk+fk_fj+fk_fb+fk_fr)*100
    fk_fr_percent = fk_fr/(fk_fk+fk_fj+fk_fb+fk_fr)*100
    print(f"Flow-keeping: {fk_fk_percent:.2f}%")
    print(f"Flow-jumping: {fk_fj_percent:.2f}%")
    print(f"Flow-breaking: {fk_fb_percent:.2f}%")
    print(f"Flow-reverting: {fk_fr_percent:.2f}%")
    print(f"TP: {fk_tp} ({fk_tp-tp}), FP: {fk_fp} ({fk_fp-fp}), FN: {fk_fn} ({fk_fn-fn})")
    fk_precision = fk_tp/(fk_tp+fk_fp)*100
    print(f"Precision: {fk_precision:.2f}% ({fk_precision-precision:.2f}%)")
    fk_recall = fk_tp/(fk_tp+fk_fn)*100
    print(f"Recall: {fk_recall:.2f}% ({fk_recall-recall:.2f}%)")
    fk_f1 = 2*fk_tp/(2*fk_tp+fk_fp+fk_fn)*100
    print(f"F1: {fk_f1:.2f}% ({fk_f1-f1:.2f}%)")
