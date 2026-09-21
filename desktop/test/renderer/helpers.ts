/** Adjacent pairs, so tests can assert on spacing without zip tricks. */
export const pairwise = <T>(items: T[]): Array<[T, T]> =>
  items.slice(0, -1).map((item, i) => [item, items[i + 1] as T]);
