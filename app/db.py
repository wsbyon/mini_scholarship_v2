"""DB 연결 한 곳. 화살표 ③⑥⑩(PostgreSQL :5432)은 전부 이 함수를 거친다."""
import os

import psycopg
from psycopg.rows import dict_row

# docker-compose.yml 에서 넘겨준다. 'postgres' = 같은 Compose 안의 DB 컨테이너 이름
DATABASE_URL = os.environ["DATABASE_URL"]


def get_conn():
    # row_factory=dict_row → 결과를 {"id": 1, "name": "김학생"} 처럼 받는다
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)