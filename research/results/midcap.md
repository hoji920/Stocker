# 미국 중형·고변동성 종목 검증 (가설 3개, 미리 정함)

- 후보 139개 중 일봉 500개 이상 136개 → 변동성 큰 순 100개 → 탐색 50 / 확증 50
- 하루 변동폭(ATR÷종가) 중앙값: 이 종목군 6.4% / 기존 미국 대형주 2.5%
- 일봉 개수 중앙값: 1777
- 판정·대조군·월 묶음 부트스트랩 2000회는 기존 검증과 같다. Holm p 는 가설 3개 기준, 결론은 확증 종목에서만

## 확증 (결론은 여기서): 50종목

ACHR, AEHR, AI, AMC, APP, ASTS, BBAI, BE, BTBT, CELH, CIFR, CLS, COHR, CORZ, CRSP, DDOG, DKNG, ELF, EOSE, FCEL, FVRR, HIMS, HUT, IOT, IREN, LCID, LITE, LSCC, MARA, MRNA, MSTR, MU, NET, NNE, NVAX, OPEN, PLTR, PTON, QS, RDW, RIOT, RKLB, SEDG, SMCI, SNAP, SNOW, SOUN, U, UPST, VRT

| 가설 | 표본 | 값 | 비교 기준 | 차이 | 95% 구간 | Holm p | 결론 |
|---|---|---|---|---|---|---|---|
| H1 자동 수평선이 대조군보다 잘 지켜진다 | 14,325 | 지켜진 비율 +54.07% | 대조군 +54.83% | -0.76%p | -1.67 ~ +0.12 | 0.261 | 확인 안 됨 |
| H2 52주 최고가 근처 종목이 20봉 뒤 더 오른다 | 11,039 | 가까움 초과수익 +0.30% | 멂 초과수익 +0.08% | +0.22%p | -3.17 ~ +3.30 | 0.915 | 확인 안 됨 |
| H3 상승형 패턴 돌파 뒤 20봉 초과수익 > 0 | 978 | 돌파 뒤 20봉 +6.40% | 그 종목 평소 20봉 +4.28% | +2.12%p | -1.43 ~ +5.77 | 0.526 | 확인 안 됨 |

## 탐색 (참고): 50종목

AA, ACLS, AFRM, ALAB, AMKR, ARM, BB, BEAM, CCJ, CHPT, CLF, CLSK, COIN, CRDO, CVNA, DELL, DUOL, ENPH, ESTC, FSLR, GTLB, HOOD, IONQ, JOBY, LEU, LMND, LUNR, MDB, MRVL, NBIS, NIO, NTLA, OKLO, PATH, PLUG, QBTS, RBLX, RGTI, RIVN, RUN, SHOP, SMR, SOFI, TTD, UEC, VKTX, VST, W, WULF, ZS

| 가설 | 표본 | 값 | 비교 기준 | 차이 | 95% 구간 | Holm p | 결론 |
|---|---|---|---|---|---|---|---|
| H1 자동 수평선이 대조군보다 잘 지켜진다 | 14,870 | 지켜진 비율 +54.30% | 대조군 +54.81% | -0.51%p | -1.37 ~ +0.40 | 0.657 | 확인 안 됨 |
| H2 52주 최고가 근처 종목이 20봉 뒤 더 오른다 | 10,999 | 가까움 초과수익 -1.42% | 멂 초과수익 +0.13% | -1.55%p | -3.95 ~ +0.89 | 0.657 | 확인 안 됨 |
| H3 상승형 패턴 돌파 뒤 20봉 초과수익 > 0 | 1,065 | 돌파 뒤 20봉 +3.97% | 그 종목 평소 20봉 +3.31% | +0.65%p | -2.62 ~ +3.73 | 0.71 | 확인 안 됨 |
