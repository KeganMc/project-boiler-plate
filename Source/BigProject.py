import os, time
from reward import reward
from workerThread import workerThread
import state
import state_manager
import memory_watcher
from controller_outputs import outputs
from controller_outputs import output_map
import random
import tensorflow as tf
import numpy as np
import random
from state_store import StateStore
from actor_critic import ActorCriticNetwork
from threading import Thread
import threading

PLAYER_RELATIONSHIP_LIST = [2, 1, 0, 0]
global threads_save
global threads_quit
"""
Find the Dolphin user directory.
"""
def find_directory():
  possible = ["~/.dolphin-emu", "~/.local/share/.dolphin-emu", "~/Library/Application Support/Dolphin", "~/.local/share/dolphin-emu"]
  for path in possible:
    fullPath = os.path.expanduser(path)
    if os.path.isdir(fullPath):
      return fullPath
  return None

"""
Find and make the pipe for the bot to send controller inputs.
    dolphinPath = the path to the dolphin user directory
"""
def find_make_pipe_dir(dolphinPath):
  pipesPath = dolphinPath + "/Pipes"
  if os.path.isdir(pipesPath):
    return pipesPath
  os.mkdir(pipesPath)
  return pipesPath

"""
Write the memory locations we want dolphin to output.
    dolphin_dir = the Dolphin user directory
    locations = the list of memory locations for Dolphin to output
"""
def write_locations(dolphin_dir, locations):
  path = dolphin_dir + '/MemoryWatcher/Locations.txt'
  with open(path, 'w') as f:
    f.write('\n'.join(locations))
  return

"""
Finds the memory watcher directory.
    dolphinPath = the path to the dolphin user directory
"""
def find_socket(dolphinPath):
  socketDir = dolphinPath + "/MemoryWatcher"
  if not os.path.isdir(socketDir):
    os.mkdir(socketDir)
  socketPath = socketDir + "/MemoryWatcher"
  return socketPath

"""
Appends the state info about a player to the state list.
    stList = the current list of state info
    st = the current state
    players = the list of playerIDs for the player info to append
"""
def appendPlayerInfoToStateList(stList, st, players):
  for playerID in players:
    player = st.players[playerID]
    stList.append(player.stocks)
    stList.append(player.cursor_x)
    stList.append(player.cursor_y)
    stList.append(player.type.value)
    stList.append(player.character.value)
    stList.append(player.action_state.value)
    stList.append(player.facing)
    stList.append(player.self_air_vel_x)
    stList.append(player.self_air_vel_y)
    stList.append(player.attack_vel_x)
    stList.append(player.attack_vel_y)
    stList.append(player.pos_x)
    stList.append(player.pos_y)
    stList.append(player.on_ground)
    stList.append(player.action_frame)
    stList.append(player.percent)
    stList.append(player.hitlag)
    stList.append(player.jumps_used)
    stList.append(player.body_state.value)

"""
Input: The current states our bot is in
    players = A list containing character information. The index of each element in the
    list reflects the id number for each character and the value reflects the character allegiance.
        0 = Does not exist in game
        1 = Our Bot
        2 = Opponent
        3 = Ally
Process: Past and current state variables such as player stocks and percentages are
    examined and a reward is assigned to our bot based off the differences in those variables
Output: Reward to be given to our bot
"""
def preprocess(st, players):
  # Find bot, then ally, then enemy
  botID = None
  allies = []
  enemies = []
  for playerID, relation in enumerate(players):
    if relation == 1:
      botID = playerID
    elif relation == 3:
      allies.append(playerID)
    else:
      enemies.append(playerID)
  stList = []
  stList.append(st.frame)
  stList.append(st.stage.value)
  appendPlayerInfoToStateList(stList, st, [botID])
  appendPlayerInfoToStateList(stList, st, enemies)
  appendPlayerInfoToStateList(stList, st, allies)
  return np.reshape(np.array(stList), [1,78])

"""
Call this function to update an ActorCritic network. The lists are reversed in this function.
    sess = tensorflow
    network = the local neural network
    actionList = the list of actions taken in order
    stateList = the list of states encountered in order
    valList = the list of values from the network in order
    rewardList = the list of rewards in order
    gamma = discount factor
"""
def updateNetwork(sess, network, actionList, stateList, valList, rewardList, gamma):
  R = valList[0]
  actionList.reverse()
  stateList.reverse()
  valList.reverse()
  rewardList.reverse()
  batch_a = []
  batch_s = []
  batch_r = []
  batch_td = []
  for(ai, ri, si, vi) in zip(actionList, rewardList, stateList, valList):
    R = ri + gamma * R
    td = R - vi
    a = np.zeros([40])
    a[ai.value] = 1
    batch_a.append(a)
    batch_s.append(si)
    batch_td.append(td)
    batch_r.append(R)
    network.apply_grads(sess, batch_a, batch_r, batch_s, batch_td, 0.01)

"""
Deprecated function use state_store.py now.
"""
def getLatestState(mw, sm):
  res = next(mw)
  while res is not None:
    sm.handle(*res)
    res = next(mw)

"""
Thread to create for each bot.
    i = the thread index/id
    sess = the tensorflow session
    network = local neural network for the specific bot
    pipeout = the pipe that the bot will send inputs to
    stateStore = the StateStore shared between all threads
    relationList = the relationships of the players to the bot
    training = whether we need to update the networks or not
    saver = the tensorflow saver for loading and saving the model
    modelName = the file name of the model saved to disk
"""
def trainingThread(i, sess, network, stateStore, relationList, training, saver, modelName, lock):
  dolphinPath = find_directory()
  if dolphinPath is None:
    print("Could not find dolphin directory!")
    return
  pipe = find_make_pipe_dir(dolphinPath) + "/pipe" + str(i)
  try:
    os.mkfifo(pipe)
  except OSError:
    pass
  pipeout = open(pipe, "w")
  botID = -1
  for pid, rel in enumerate(relationList):
    if rel == 1:
      botID = pid
  print("Player " + str(botID+1) + " is pipe " + pipe)
  st = state.State()
  stateManager = state_manager.StateManager(st)
  write_locations(dolphinPath, stateManager.locations())
  last_frame = 0
  actionList = []
  stateList = []
  valList = []
  rewardList = []
  lastState = None
  global threads_save
  global threads_quit
  pipeout.write(output_map[outputs.RESET])
  pipeout.flush()
  network.sync_weights(sess)
  while(True):
    res = stateStore.getNextState()
    while(res is None):
      res = stateStore.getNextState()
    stateManager.handle(*res)
    if st.frame > last_frame+3:
      last_frame = st.frame
      if st.menu == state.Menu.Game:
        currentState = preprocess(st, relationList)
        if lastState is not None:
          rewardList.append(reward(lastState, st, relationList))
        if len(valList) >= 64:
          if training:
            updateNetwork(sess, network, actionList, stateList, valList, rewardList, 0.99)
            lock.acquire()
            if threads_save:
              saver.save(sess, './saves/' + modelName)
              threads_save = False
            lock.release()
            lock.acquire()
            if threads_quit:
              lock.release()
              break
            lock.release()
          network.sync_weights(sess)
          actionList = []
          stateList = []
          valList = []
          rewardList = []
        action, val =  network.run_policy_and_value(sess, currentState)
        chosenAction = np.random.choice(list(outputs), p=action)
        actionList.append(chosenAction)
        valList.append(val)
        stateList.append(currentState)
        lastState = st
        pipeout.write(output_map[chosenAction])
        pipeout.flush()

  pipeout.close()

"""
Create the bots and start to run them.
    botRelations = A list of relations
        0 = nothing
        1 = bot index
        2 = enemy
        3 = ally
"""
def runBots(botRelations=[[2,1,3,0], [2,3,1,0]], training=True, loading=False, modelName='my-model'):
  dolphinPath = find_directory()
  if dolphinPath is None:
    print("Could not find dolphin directory!")
    return

  global threads_save
  global threads_quit
  threads_save = False
  threads_quit = False
  mwLocation = find_socket(dolphinPath)
  lock = threading.Lock()
  with memory_watcher.MemoryWatcher(mwLocation) as mw:
    stateStore = StateStore(mw)
    with tf.Session() as sess:
      learning_rate_tensor = tf.placeholder(tf.float32)
      optimizer = tf.train.RMSPropOptimizer(learning_rate=learning_rate_tensor, decay=0.9)
      globalNetwork = ActorCriticNetwork(40, optimizer)
      globalNetwork.set_up_loss(0.01)
      globalNetwork.set_up_apply_grads(learning_rate_tensor, globalNetwork.get_vars())
      saver = tf.train.Saver(globalNetwork.get_vars())
      threads = []
      for threadIndex in range(len(botRelations)):
        threadNet = ActorCriticNetwork(40, optimizer)
        threadNet.set_up_loss(0.01)
        threadNet.set_up_apply_grads(learning_rate_tensor, globalNetwork.get_vars())
        threadNet.set_up_sync_weights(globalNetwork.get_vars())
        threads.append(Thread(target=trainingThread,
                              args=(threadIndex, sess, threadNet, 
                                    stateStore, botRelations[threadIndex],
                                    training, saver, modelName, lock)))
      #TODO: Only initialize variables as needed.
      sess.run(tf.global_variables_initializer())
      if loading:
        saver.restore(sess, './saves/' + modelName)
      for thread in threads:
        thread.start()
      print("Enter 'save' to save the model and 'quit' to quit the program:")
      while True:
        com = input()
        if com == "save":
          lock.acquire()
          threads_save = True
          lock.release()
        if com == "quit":
          lock.acquire()
          threads_quit = True
          lock.release()
          break
      for thread in threads:
        thread.join()

"""
Deprecated main function
"""
def main():
  print("Train bot? (y/n)")
  ans = input()
  train = False
  if ans == 'y':
    train = True
  filesSaved = [f for f in os.listdir('./saves') 
               if os.path.isfile(os.path.join('./saves', f))]
  files = []
  fileCounter = 1
  print('Which model would you like to load? (0 for new model)')
  for f in filesSaved:
    if f.endswith('.index'):
      files.append(f[:-6])
      print(str(fileCounter) + ': ' + f[:-6])
      fileCounter += 1
  ansInt = int(input())
  mName = ''
  load = False
  if ansInt > 0:
    load = True
    mName = files[ansInt - 1]
  elif training:
    print('Please enter a name for the new model')
    mName = input()
  runBots(training=train, loading=load, modelName=mName)

if __name__=="__main__": main()
