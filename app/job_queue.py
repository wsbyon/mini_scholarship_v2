"""큐 모듈: Redis 를 쓴다는 사실은 이 파일 밖으로 나가지 않는다. (SQS 등으로 바꿀 때 여기만 고침)
화살표 ④⑫ = enqueue (LPUSH),  ⑤ = dequeue (BRPOP)"""
import json
import os

import redis

# socket_timeout: Redis 응답을 기다리는 최대 시간. BRPOP 대기(5초)보다 길게 잡아야 한다
_r = redis.Redis.from_url(os.environ["REDIS_URL"], decode_responses=True, socket_timeout=30)


def enqueue(queue, job):
    # LPUSH: 목록 왼쪽에 넣는다. job 은 dict → JSON 글자로 바꿔서 저장
    _r.lpush(queue, json.dumps(job, ensure_ascii=False))


def dequeue(queues, timeout=5):
    # BRPOP: 목록 오른쪽에서 꺼낸다. 비어 있으면 timeout 초까지 기다렸다가 None
    try:
        item = _r.brpop(queues, timeout=timeout)
    except redis.exceptions.TimeoutError:
        return None                      # 혹시 시간 초과가 나도 "일 없음"으로 보고 다시 기다린다
    if item is None:
        return None
    queue, raw = item
    return queue, json.loads(raw)