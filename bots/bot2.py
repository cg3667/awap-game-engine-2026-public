import random
from collections import deque
from typing import Tuple, Optional, List

from game_constants import Team, TileType, FoodType, ShopCosts
from robot_controller import RobotController
from item import Pan, Plate, Food

class BotPlayer:
    def __init__(self, map_copy):
        self.map = map_copy
        self.assembly_counter = None
        self.cooker_loc = None
        self.sink_loc = None
        self.sink_table_loc = None
        self.my_bot_id = None
        self.found_order = False
        self.current_order = None
        self.state = 0
        self.ingredients = None
        self.tasks = [] 
        self.assignments = {}

    def play_turn(self, controller: RobotController):
        my_bots = controller.get_team_bot_ids(controller.get_team())
        if not my_bots: return

        #Refill queue
        if not self.tasks and not self.assignments:
            self.generate_tasks_from_order(controller)

        #Assign and execute
        for bot_id in my_bots:
            # Check if bot needs a job
            if bot_id not in self.assignments or self.assignments[bot_id] is None:
                if self.tasks:
                    # Pop the next task and assign it
                    self.assignments[bot_id] = self.tasks.pop(0)
                else:
                    # No tasks left? Park the bot so it doesn't block traffic
                    self.move_towards(controller, bot_id, 1, 1) 
                    continue

            # Execute the assigned task
            current_task = self.assignments[bot_id]
            task_finished = self.execute_task(bot_id, current_task, controller)

            # 3. Cleanup: If the task is done, clear the assignment
            if task_finished:
                # If the finished task was a SUBMIT, reset the global state
                if current_task["type"] == "SUBMIT":
                    self.reset_kitchen_state()
                
                # Clear this bot's assignment so it gets a new task next turn
                self.assignments[bot_id] = None
    

    def reset_kitchen_state(self):
        """Clears flags so the bot knows to look for a new order."""
        self.found_order = False
        self.current_order = None
        self.ingredients = None

    def generate_tasks_from_order(self, controller):
        current_orders = controller.get_orders(controller.get_team())
        best_order = None
        for order in current_orders:
            if order["is_active"] and (order["expires_turn"] - controller.get_turn() > len(order["required"])):
                if best_order is None or order["reward"] > best_order["reward"]:
                    best_order = order
        if not best_order:
            return

        self.current_order = best_order
        self.found_order = True
        self.tasks = []

        self.tasks.append({"type": "GET_PLATE", "item": ShopCosts.PLATE, "loc": self.sink_table_loc})
        self.tasks.append({"type": "BUY_AND_POUR", "item": FoodType.NOODLES, "loc": "SHOP"})

        for ingredient_id in self.current_order["required"]:
            if ingredient_id == 0: # EGG
                self.tasks.append({"type": "COOK_TASK", "item": FoodType.EGG})
            elif ingredient_id == 1: # ONION
                self.tasks.append({"type": "CHOP_TASK", "item": FoodType.ONION})
            elif ingredient_id == 2: # MEAT
                self.tasks.append({"type": "CHOP_AND_COOK_TASK", "item": FoodType.MEAT})
            elif ingredient_id == 4: # SAUCE
                self.tasks.append({"type": "BUY_AND_POUR", "item": FoodType.SAUCE})
        self.tasks.append({"type": "SUBMIT","item": None, "loc": "SUBMIT_TILE"})
        self.tasks.append({"type": "WASH_PLATE", "item": None})


    def execute_task(self, bot_id, task, controller):
        bot_info = controller.get_bot_state(bot_id)
        bx, by = bot_info['x'], bot_info['y']
        item_held = bot_info.get('holding')

        food_type = task["item"]
        t_type = task["type"]
        
        # 1. Ensure Critical Locations are found
        if not self.assembly_counter:
            self.assembly_counter = self.find_nearest_tile(controller, bx, by, "COUNTER")
        if not self.sink_table_loc:
            self.sink_table_loc = self.find_nearest_tile(controller, bx, by, "SINKTABLE")
        if not self.cooker_loc:
            self.cooker_loc = self.find_nearest_tile(controller, bx, by, "COOKER")

        # 2. Safety Check
        if not self.assembly_counter or not self.sink_table_loc:
            return False
            
        cx, cy = self.assembly_counter
        stx, sty = self.sink_table_loc
        kx, ky = self.cooker_loc if self.cooker_loc else (0,0)

        prep_pos = self.find_nearest_tile(controller, bx, by, "COUNTER")
        if not prep_pos:
            return False
        prep_x, prep_y = prep_pos

        if t_type == "GET_PLATE":
            if not item_held:
                # Check SinkTable for a free clean plate
                sink_tile = controller.get_tile(controller.get_team(), stx, sty)
                if sink_tile and sink_tile.tile_name == "SinkTable" and sink_tile.item:
                    if isinstance(sink_tile.item, Plate) and not sink_tile.item.is_dirty:
                        if self.move_towards(controller, bot_id, stx, sty):
                            controller.take_clean_plate(bot_id, stx, sty)
                        return False

                # Buy if SinkTable is empty
                shop_pos = self.find_nearest_tile(controller, bx, by, "Shop")
                if shop_pos:
                    sx, sy = shop_pos
                    if self.move_towards(controller, bot_id, sx, sy):
                        # Fix tuple error: try value, then index, then default
                        cost = getattr(ShopCosts.PLATE, 'value', 2)
                        if isinstance(ShopCosts.PLATE, (tuple, list)): cost = ShopCosts.PLATE[0]
                        else:
                            cost = getattr(ShopCosts.PLATE, 'value', 2)
                        if controller.get_team_money() >= cost:
                            controller.buy(bot_id, FoodType.PLATE, sx, sy)
                return False

            elif isinstance(item_held, Plate):
                if self.move_towards(controller, bot_id, cx, cy):
                    if controller.place(bot_id, cx, cy):
                        return True
            return False


        elif t_type == "COOK_TASK":
            if not item_held:
                shop_pos = self.find_nearest_tile(controller, bx, by, "SHOP")
                if self.move_towards(controller, bot_id, shop_pos[0], shop_pos[1]):
                    if controller.get_team_money(controller.get_team()) >= food_type.buy_cost:
                        controller.buy(bot_id, food_type, shop_pos[0], shop_pos[1])
                return False 

            if isinstance(item_held, Food) and item_held.type == food_type and item_held.cooked_stage == 0:
                if self.move_towards(controller, bot_id, kx, ky):
                    tile = controller.get_tile(controller.get_team(), kx, ky)
                    if tile and isinstance(tile.item, Pan) and tile.item.food is None:
                        controller.place(bot_id, kx, ky)
                return False

            # STEP 3: Wait and watch the pan
            tile = controller.get_tile(controller.get_team(), kx, ky)
            if tile and isinstance(tile.item, Pan) and tile.item.food and tile.item.food.type == food_type:
                food_in_pan = tile.item.food
                if food_in_pan.cooked_stage == 1: 
                    if self.move_towards(controller, bot_id, kx, ky):
                        controller.take_from_pan(bot_id, kx, ky)
                elif food_in_pan.cooked_stage == 2:
                    if self.move_towards(controller, bot_id, kx, ky):
                        controller.take_from_pan(bot_id, kx, ky)
                return False

            # STEP 4: Plating
            if isinstance(item_held, Food) and item_held.type == food_type and item_held.cooked_stage == 1:
                tile = controller.get_tile(controller.get_team(), cx, cy)
                if not (tile and isinstance(tile.item, Plate)):
                    return False
                if self.move_towards(controller, bot_id, cx, cy):
                    if controller.add_food_to_plate(bot_id, cx, cy):
                        return True

            # STEP 5: Trash
            if isinstance(item_held, Food) and item_held.cooked_stage == 2:
                trash_pos = self.find_nearest_tile(controller, bx, by, "TRASH")
                if self.move_towards(controller, bot_id, trash_pos[0], trash_pos[1]):
                    controller.trash(bot_id, trash_pos[0], trash_pos[1])
                return False
            return False

        elif t_type == "CHOP_TASK":
            if not item_held:
                shop_pos = self.find_nearest_tile(controller, bx, by, "SHOP")
                if self.move_towards(controller, bot_id, shop_pos[0], shop_pos[1]):
                    if controller.get_team_money(controller.get_team()) >= food_type.buy_cost:
                        controller.buy(bot_id, food_type, shop_pos[0], shop_pos[1])
                return False

            if isinstance(item_held, Food) and item_held.type == food_type and not item_held.is_chopped:
                if self.move_towards(controller, bot_id, prep_x, prep_y):
                    tile = controller.get_tile(controller.get_team(), prep_x, prep_y)
                    if tile and tile.item is None:
                        controller.place(bot_id, prep_x, prep_y)
                return False

            tile = controller.get_tile(controller.get_team(), prep_x, prep_y)
            if tile and isinstance(tile.item, Food) and tile.item.type == food_type:
                if not tile.item.is_chopped:
                    if self.move_towards(controller, bot_id, prep_x, prep_y):
                        controller.chop(bot_id, prep_x, prep_y)
                    return False
                else:
                    if self.move_towards(controller, bot_id, prep_x, prep_y):
                        controller.pickup(bot_id, prep_x, prep_y)
                    return False

            if isinstance(item_held, Food) and item_held.type == food_type and item_held.is_chopped:
                tile = controller.get_tile(controller.get_team(), cx, cy)
                if not (tile and isinstance(tile.item, Plate)):
                    return False 
                if self.move_towards(controller, bot_id, cx, cy):
                    if controller.add_food_to_plate(bot_id, cx, cy):
                        return True
            return False
        
        elif t_type == "CHOP_AND_COOK_TASK":
            if not item_held:
                shop_pos = self.find_nearest_tile(controller, bx, by, "SHOP")
                if self.move_towards(controller, bot_id, shop_pos[0], shop_pos[1]):
                    if controller.get_team_money(controller.get_team()) >= food_type.buy_cost:
                        controller.buy(bot_id, food_type, shop_pos[0], shop_pos[1])
                return False

            if isinstance(item_held, Food) and item_held.type == food_type and not item_held.is_chopped:
                if self.move_towards(controller, bot_id, prep_x, prep_y):
                    tile = controller.get_tile(controller.get_team(), prep_x, prep_y)
                    if tile and tile.item is None:
                        controller.place(bot_id, prep_x, prep_y)
                return False

            tile = controller.get_tile(controller.get_team(), prep_x, prep_y)
            if tile and isinstance(tile.item, Food) and tile.item.type == food_type:
                if not tile.item.is_chopped:
                    if self.move_towards(controller, bot_id, prep_x, prep_y):
                        controller.chop(bot_id, prep_x, prep_y)
                    return False
                else:
                    if self.move_towards(controller, bot_id, prep_x, prep_y):
                        controller.pickup(bot_id, prep_x, prep_y)
                    return False
            
            if isinstance(item_held, Food) and item_held.type == food_type and item_held.is_chopped and item_held.cooked_stage == 0:
                if self.move_towards(controller, bot_id, kx, ky):
                    tile = controller.get_tile(controller.get_team(), kx, ky)
                    if tile and isinstance(tile.item, Pan) and tile.item.food is None:
                        controller.place(bot_id, kx, ky)
                return False

            tile = controller.get_tile(controller.get_team(), kx, ky)
            if tile and isinstance(tile.item, Pan) and tile.item.food and tile.item.food.type == food_type:
                food_in_pan = tile.item.food
                if food_in_pan.cooked_stage == 1: 
                    if self.move_towards(controller, bot_id, kx, ky):
                        controller.take_from_pan(bot_id, kx, ky)
                elif food_in_pan.cooked_stage == 2:
                    if self.move_towards(controller, bot_id, kx, ky):
                        controller.take_from_pan(bot_id, kx, ky)
                return False

            if isinstance(item_held, Food) and item_held.type == food_type and item_held.cooked_stage == 1:
                tile = controller.get_tile(controller.get_team(), cx, cy)
                if not (tile and isinstance(tile.item, Plate)):
                    return False
                if self.move_towards(controller, bot_id, cx, cy):
                    if controller.add_food_to_plate(bot_id, cx, cy):
                        return True

            if isinstance(item_held, Food) and item_held.cooked_stage == 2:
                trash_pos = self.find_nearest_tile(controller, bx, by, "TRASH")
                if self.move_towards(controller, bot_id, trash_pos[0], trash_pos[1]):
                    controller.trash(bot_id, trash_pos[0], trash_pos[1])
                return False
            return False

        elif t_type == "BUY_AND_POUR":
            if not item_held:
                shop_pos = self.find_nearest_tile(controller, bx, by, "SHOP")
                if self.move_towards(controller, bot_id, shop_pos[0], shop_pos[1]):
                    if controller.get_team_money(controller.get_team()) >= food_type.buy_cost:
                        controller.buy(bot_id, food_type, shop_pos[0], shop_pos[1])
                return False
            if isinstance(item_held, Food) and item_held.type == food_type:
                tile = controller.get_tile(controller.get_team(), cx, cy)
                if not (tile and isinstance(tile.item, Plate)):
                    return False
                if self.move_towards(controller, bot_id, cx, cy):
                    if controller.add_food_to_plate(bot_id, cx, cy):
                        return True
            return False

        elif t_type == "SUBMIT":
            submit_pos = self.find_nearest_tile(controller, bx, by, "SUBMIT_TILE")
            if not submit_pos: return False
            ux, uy = submit_pos

            if not isinstance(item_held, Plate):
                if self.move_towards(controller, bot_id, cx, cy):
                    controller.pickup(bot_id, cx, cy)
                return False
            else:
                if self.move_towards(controller, bot_id, ux, uy):
                    if controller.submit(bot_id, ux, uy):
                        return True
            return False

        elif t_type == "WASH_PLATE":
            submit_pos = self.find_nearest_tile(controller, bx, by, "Submit")
            sink_pos = self.find_nearest_tile(controller, bx, by, "Sink")
            if not submit_pos or not sink_pos: return False
            ux, uy = submit_pos
            sx, sy = sink_pos

            # STEP 1: Get dirty plate from submission window
            if not item_held:
                if self.move_towards(controller, bot_id, ux, uy):
                    controller.pickup(bot_id, ux, uy)
                return False

            # STEP 2: Put dirty plate in Sink
            if isinstance(item_held, Plate) and item_held.is_dirty:
                if self.move_towards(controller, bot_id, sx, sy):
                    controller.put_dirty_plate_in_sink(bot_id, sx, sy)
                return False

            # STEP 3: Wash the dirty plate in the sink
            sink_tile = controller.get_tile(controller.get_team(), sx, sy)
            if sink_tile and sink_tile.tile_name == "Sink" and sink_tile.item:
                # Based on doc: sink contains a dirty plate
                if getattr(sink_tile.item, 'num_dirty_plates', 0) > 0:
                    if self.move_towards(controller, bot_id, sx, sy):
                        controller.wash_sink(bot_id, sx, sy)
                    return False
                
                # STEP 4: Pickup clean plate from sink to move to SinkTable
                if getattr(sink_tile.item, 'num_clean_plates', 0) > 0:
                    if self.move_towards(controller, bot_id, sx, sy):
                        controller.pickup(bot_id, sx, sy)
                    return False

            # STEP 5: Store clean plate on SinkTable
            if isinstance(item_held, Plate) and not item_held.is_dirty:
                if self.move_towards(controller, bot_id, stx, sty):
                    if controller.place(bot_id, stx, sty):
                        return True
            return False





    def get_bfs_path(self, controller: RobotController, start: Tuple[int, int], target_predicate, obstacles: set) -> Optional[Tuple[int, int]]:
        queue = deque([(start, [])])
        visited = set([start])
        visited.update(obstacles)
        w, h = self.map.width, self.map.height

        while queue:
            (curr_x, curr_y), path = queue.popleft()
            tile = controller.get_tile(controller.get_team(), curr_x, curr_y)
            if target_predicate(curr_x, curr_y, tile):
                if not path: return (0, 0)
                return path[0]

            for dx in [0, -1, 1]:
                for dy in [0, -1, 1]:
                    if dx == 0 and dy == 0: continue
                    nx, ny = curr_x + dx, curr_y + dy
                    if 0 <= nx < w and 0 <= ny < h and (nx, ny) not in visited:
                        if controller.get_map(controller.get_team()).is_tile_walkable(nx, ny):
                            visited.add((nx, ny))
                            queue.append(((nx, ny), path + [(dx, dy)]))
        return None

    def move_towards(self, controller: RobotController, bot_id: int, target_x: int, target_y: int) -> bool:
        bot_state = controller.get_bot_state(bot_id)
        bx, by = bot_state['x'], bot_state['y']
        all_team_bots = controller.get_team_bot_ids(controller.get_team())
        obstacles = set()
        for other_id in all_team_bots:
            if other_id != bot_id:  
                other_state = controller.get_bot_state(other_id)
                obstacles.add((other_state['x'], other_state['y']))

        def is_adjacent_to_target(x, y, tile):
            return max(abs(x - target_x), abs(y - target_y)) <= 1
        if is_adjacent_to_target(bx, by, None): return True
        step = self.get_bfs_path(controller, (bx, by), is_adjacent_to_target, obstacles)
        if step and (step[0] != 0 or step[1] != 0):
            controller.move(bot_id, step[0], step[1])
            return False
        return False

    def find_nearest_tile(self, controller: RobotController, bot_x: int, bot_y: int, tile_name: str) -> Optional[Tuple[int, int]]:
        best_dist = 9999
        best_pos = None
        m = controller.get_map(controller.get_team())
        for x in range(m.width):
            for y in range(m.height):
                tile = m.tiles[x][y]
                if tile.tile_name == tile_name:
                    dist = max(abs(bot_x - x), abs(bot_y - y))
                    if dist < best_dist:
                        best_dist = dist
                        best_pos = (x, y)
        return best_pos
