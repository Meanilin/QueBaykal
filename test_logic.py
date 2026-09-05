"""Юнит-тесты логики очереди (без Telegram)."""
import asyncio

from bot import MovieStore


async def main() -> None:
    s = MovieStore()

    a = await s.create("Интерстеллар", added_by=1)
    b = await s.create("Начало", added_by=2)
    c = await s.create("Дюна", added_by=3)
    assert (a.movie_id, b.movie_id, c.movie_id) == (1, 2, 3)

    # голоса
    await s.toggle_vote(a.movie_id, 10)
    await s.toggle_vote(a.movie_id, 11)
    await s.toggle_vote(a.movie_id, 12)
    await s.toggle_vote(b.movie_id, 10)
    await s.toggle_vote(c.movie_id, 20)
    assert a.votes == 3 and b.votes == 1 and c.votes == 1

    sorted_movies = await s.all_sorted()
    assert [m.title for m in sorted_movies] == ["Интерстеллар", "Начало", "Дюна"]

    # повторное нажатие — снимает голос
    await s.toggle_vote(a.movie_id, 10)
    assert a.votes == 2

    # удаление: чужой — нельзя
    assert (await s.delete(b.movie_id, user_id=99, is_admin=False)) is False
    # владелец — можно
    assert (await s.delete(b.movie_id, user_id=2, is_admin=False)) is True

    # формирование очереди
    top = sorted_movies[0]
    built = await s.build_queue(top.movie_id)
    assert built is not None and built.watchers == sorted(built.voters)

    print("ALL OK")


if __name__ == "__main__":
    asyncio.run(main())
